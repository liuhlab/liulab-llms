"""A task's state file: one SQLite file per task, and what a reader gets back from it."""

import atexit
import contextlib
import getpass
import json
import os
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from labllm.model import Model

if TYPE_CHECKING:
    from labllm.reviews import Review

#: The endings a pass gives. A person's review gives the fourth, dropped.
PASS_ENDINGS = ("settled", "unsettled", "refused")
#: The endings: what a unit's current answer can be.
ENDINGS = (*PASS_ENDINGS, "dropped")
#: What else a unit's outcome in one pass can be.
NOT_ENDINGS = ("unfinished", "held")

#: Marks a SQLite file as a task's state file.
_APPLICATION_ID = 0x6C6C6D74

#: Each script upgrades the file from the layout numbered by its index to the next one. A new
#: layout is a new script at the end, never an edit to one already released.
_UPGRADES: tuple[str, ...] = (
    """
    CREATE TABLE passes (
        pass_id INTEGER PRIMARY KEY,
        meter TEXT NOT NULL,
        kind TEXT NOT NULL,
        kind_version TEXT NOT NULL,
        label TEXT NOT NULL,
        models TEXT NOT NULL,
        ceiling_usd REAL NOT NULL,
        off_peak_only INTEGER NOT NULL,
        repo_version TEXT NOT NULL,
        labllm_version TEXT NOT NULL,
        stopped TEXT,
        spent_usd REAL,
        first_cost_usd REAL,
        requests INTEGER,
        cache_hits INTEGER
    );
    CREATE TABLE outcomes (
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        pass_id INTEGER NOT NULL REFERENCES passes,
        ending TEXT NOT NULL,
        inputs_tag TEXT,
        answer TEXT,
        reason TEXT,
        PRIMARY KEY (kind, key, pass_id)
    );
    CREATE TABLE checks (
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        pass_id INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        name TEXT NOT NULL,
        message TEXT,
        PRIMARY KEY (kind, key, pass_id, seq),
        FOREIGN KEY (kind, key, pass_id) REFERENCES outcomes
    );
    """,
    """
    ALTER TABLE outcomes ADD COLUMN spent_usd REAL;
    ALTER TABLE outcomes ADD COLUMN first_cost_usd REAL;
    ALTER TABLE outcomes ADD COLUMN requests INTEGER;
    ALTER TABLE outcomes ADD COLUMN cache_hits INTEGER;
    ALTER TABLE outcomes ADD COLUMN usage TEXT;
    ALTER TABLE outcomes ADD COLUMN seconds REAL;
    """,
    """
    ALTER TABLE passes ADD COLUMN note TEXT;
    """,
)

#: The layout this version of liulab-llms writes.
LAYOUT = len(_UPGRADES)


#: How often a holder touches its holder file, and how long it may go untouched before a
#: process on another host counts the holder as gone.
HEARTBEAT_S = 20.0
STALE_S = 120.0

#: How long a read-only read waits for a write in progress, and how long a write waits for
#: reads in progress. A write waits far longer, so no reader makes a pass fail.
READ_WAIT_S = 5.0
WRITE_WAIT_S = 120.0


class UnknownLayout(Exception):  # noqa: N818 - the name callers catch
    """The state file is not one this version of liulab-llms can read."""


class TaskInUse(Exception):  # noqa: N818 - the name callers catch
    """Another process holds the task, or has taken it over from this one."""


class NoTask(Exception):  # noqa: N818 - the name callers catch
    """A read-only open found no task: no folder, no state file, or one never written."""


class TaskBusy(Exception):  # noqa: N818 - the name callers catch
    """A write in progress kept a read waiting past `READ_WAIT_S`. Try the read again."""


class TaskNeedsRepair(Exception):  # noqa: N818 - the name callers catch
    """A pass stopped in the middle of a write. Open the task once with `Task` to repair it."""


class Holder:
    """A process's hold on a task: a holder file made only if absent, touched while held.

    A holder counts as gone once its process has ended, if it ran on this host, or once its
    file has gone `STALE_S` seconds untouched; the next process to open the task then takes
    over. A holder whose file was taken over refuses every later write.
    """

    def __init__(self, path: Path, folder: Path) -> None:
        self.path, self._folder = path, folder
        me = {
            "user": _user(),
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "since": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        for _ in range(3):
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                break
            except FileExistsError:
                other = _read_holder(path)
                if other is None:
                    continue
                if not _gone(*other):
                    raise TaskInUse(
                        f"{folder} is held by {_describe(other[0])}. A holder counts as gone "
                        f"once its process ends, or once {path} goes {STALE_S:.0f} s untouched"
                    ) from None
                path.unlink(missing_ok=True)
        else:
            raise TaskInUse(f"{folder} is being opened by another process")
        os.write(fd, json.dumps(me).encode())
        os.fsync(fd)
        self._fd: int | None = fd
        self._id = _file_id(os.fstat(fd))
        self._stop = threading.Event()
        self._beat = threading.Thread(
            target=self._touch, args=(fd,), name="labllm task holder", daemon=True
        )
        self._beat.start()
        atexit.register(self.release)

    def _touch(self, fd: int) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            with contextlib.suppress(OSError):
                os.utime(fd)

    def check(self) -> None:
        """Raise `TaskInUse` if another process has taken the task over."""
        try:
            now = _file_id(self.path.stat())
        except FileNotFoundError:
            now = None
        if now != self._id:
            other = _read_holder(self.path)
            who = "another process" if other is None else _describe(other[0])
            raise TaskInUse(f"{self._folder} is now held by {who}; this process lost its hold")

    def release(self) -> None:
        """Give up the hold, removing the holder file if it is still this process's."""
        if self._fd is None:
            return
        self._stop.set()
        self._beat.join()
        with contextlib.suppress(FileNotFoundError):
            if _file_id(self.path.stat()) == self._id:
                self.path.unlink()
        os.close(self._fd)
        self._fd = None
        atexit.unregister(self.release)


@dataclass(frozen=True)
class TaskHolder:
    """The process holding a task, as its holder file records it.

    Each field is None when the file does not give it, as while its maker is still writing it.
    """

    user: str | None
    pid: int | None
    host: str | None
    since: datetime | None


def holder_of(state: Path) -> TaskHolder | None:
    """Return who holds the task whose state file is `state`, or None if no process does.

    A holder counts as gone by the same rule a writing open applies. Nothing is written: a gone
    holder's file stays until a writing open deletes it.
    """
    found = _read_holder(_holder_path(state))
    if found is None or _gone(*found):
        return None
    info = found[0]
    since = None
    if isinstance(text := info.get("since"), str):
        with contextlib.suppress(ValueError):
            since = datetime.fromisoformat(text)
    return TaskHolder(
        user=info.get("user"), pid=info.get("pid"), host=info.get("host"), since=since
    )


def _holder_path(state: Path) -> Path:
    return state.with_suffix(".holder")


def _read_holder(path: Path) -> tuple[dict[str, Any], float] | None:
    """Return the holder file's contents and seconds since it was touched, or None if gone."""
    try:
        with path.open("rb") as f:
            age = time.time() - os.fstat(f.fileno()).st_mtime
            data = f.read()
    except FileNotFoundError:
        return None
    try:
        info = json.loads(data)
    except ValueError:
        info = {}  # its maker has not written it yet
    return (info if isinstance(info, dict) else {}), age


def _gone(info: dict[str, Any], age: float) -> bool:
    if age > STALE_S:
        return True
    pid = info.get("pid")
    if info.get("host") != socket.gethostname() or not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _describe(info: dict[str, Any]) -> str:
    if not info:
        return "a process that is still opening it"
    return (
        f"{info.get('user')}'s process {info.get('pid')} on {info.get('host')}, "
        f"since {info.get('since')}"
    )


def _file_id(st: os.stat_result) -> tuple[int, int]:
    return st.st_dev, st.st_ino


def _user() -> str:
    try:
        return getpass.getuser()
    except OSError:
        return "unknown"


@dataclass(frozen=True)
class CheckResult:
    """One check's result for one unit in one pass. `message` is None when it passed."""

    name: str
    message: str | None

    @property
    def passed(self) -> bool:
        """Whether the check passed."""
        return self.message is None


@dataclass(frozen=True)
class Stamp:
    """What produced an answer: the kind, the models, the inputs, the pass and the code.

    `pass_id` numbers the pass within its task; `meter` is the pass's identity in the gateway's
    record. `inputs_tag` is None when the unit was held before its inputs loaded.
    """

    kind: str
    kind_version: str
    models: dict[str, Model]
    inputs_tag: str | None
    pass_id: int
    meter: str
    repo_version: str
    labllm_version: str


@dataclass(frozen=True)
class UnitCost:
    """What one unit's calls cost in one pass, and how long the unit took.

    `spent_usd` is what its calls paid now, zero for a cache replay; `first_cost_usd` is what
    the same replies cost when first paid. `requests` counts the requests its calls sent
    providers, retries included, and `cache_hits` the calls the cache answered, which send
    none. `usage` sums the numbers in the replies' usage, key by key, replays
    included. `seconds` runs on the gateway client's clock from the unit's first request to
    its ending; None if it sent none.
    """

    spent_usd: float = 0.0
    first_cost_usd: float = 0.0
    requests: int = 0
    cache_hits: int = 0
    usage: dict[str, Any] = field(default_factory=dict[str, Any])
    seconds: float | None = None


@dataclass(frozen=True)
class Outcome:
    """How one unit came out of one pass.

    `ending` is one of `ENDINGS` or `NOT_ENDINGS`. `answer` is None unless the unit ended
    settled or unsettled. `reason` says why it was refused, unfinished, held or dropped.
    `cost` is None for an outcome recorded by a liulab-llms that kept no cost per unit.

    `review` is set only on a current answer that a person's review gives. Then `ending`,
    `answer`, `checks` and `reason` are the review's, and `stamp` and `cost` are those of
    the pass it outranks.
    """

    key: str
    ending: str
    answer: Any
    reason: str | None
    checks: tuple[CheckResult, ...]
    stamp: Stamp
    cost: UnitCost | None
    review: "Review | None" = None


@dataclass(frozen=True)
class PassStart:
    """What a pass records about itself before its first unit."""

    meter: str
    kind: str
    kind_version: str
    label: str
    models: dict[str, Model]
    ceiling_usd: float
    off_peak_only: bool
    repo_version: str
    labllm_version: str
    note: str | None = None


@dataclass(frozen=True)
class Pass:
    """One pass of a kind, as its task recorded it.

    The fields up to `labllm_version` are what the pass was started with; `note` is None if it
    was given none. `ended` is False for a pass that has not ended: one still running, or one
    whose process was killed or interrupted. `stopped` is the reason of the error that
    stopped it, a `Stopped` or a `Rejected` one, or None. `counts` has every ending and not-ending a pass gives, zeros
    included, over the units it recorded. The dollars, requests and cache hits are its meter's
    counters, or, for a pass that has not ended, the sums over the units it recorded.
    """

    pass_id: int
    meter: str
    kind: str
    kind_version: str
    label: str
    note: str | None
    models: dict[str, Model]
    ceiling_usd: float
    off_peak_only: bool
    repo_version: str
    labllm_version: str
    ended: bool
    stopped: str | None
    counts: dict[str, int]
    spent_usd: float
    first_cost_usd: float
    requests: int
    cache_hits: int


class StateFile:
    """One task's SQLite file. Every method is safe from many threads.

    Opened for writing, it holds the task and upgrades an older layout. Opened `read_only`, it
    takes no hold, writes nothing and refuses an older layout; each read, and each
    `snapshot`, sees what one committed transaction left.

    Raises
    ------
    TaskInUse
        Another process holds the task, on a writing open.
    NoTask
        On a read-only open, the file is missing or no task was ever written to it.
    UnknownLayout
        The file is not one this version of liulab-llms can read, or, on a read-only open, it
        has an older layout.
    TaskBusy
        On a read-only open, a write in progress kept the open waiting past `READ_WAIT_S`.
    TaskNeedsRepair
        On a read-only open, a pass stopped in the middle of a write.
    """

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.read_only = read_only
        self._lock = threading.RLock()
        self._depth = 0
        if read_only:
            self._holder: Holder | None = None
            self._conn = _connect_read_only(path)
            try:
                self._check_layout(path)
            except BaseException:
                self._conn.close()
                raise
            return
        self._holder = Holder(_holder_path(path), path.parent)
        try:
            self._conn = sqlite3.connect(path, check_same_thread=False, timeout=WRITE_WAIT_S)
        except BaseException:
            self._holder.release()
            raise
        try:
            self._open_layout(path)
        except BaseException:
            self._conn.close()
            self._holder.release()
            raise

    def _open_layout(self, path: Path) -> None:
        conn = self._conn
        try:
            # WAL needs shared memory, which a network file system does not give.
            conn.execute("PRAGMA journal_mode=DELETE")
            app, layout, empty = _layout(conn)
        except sqlite3.DatabaseError as e:
            raise UnknownLayout(f"{path} is not a task's state file: {e}") from e
        _refuse_unknown(path, app, layout, empty)
        if layout < LAYOUT:
            steps = "".join(_UPGRADES[layout:])
            conn.executescript(
                f"BEGIN; {steps}; PRAGMA application_id={_APPLICATION_ID}; "
                f"PRAGMA user_version={LAYOUT}; COMMIT;"
            )
        conn.execute("PRAGMA foreign_keys=ON")

    def _check_layout(self, path: Path) -> None:
        try:
            with self.snapshot() as conn:
                app, layout, empty = _layout(conn)
        except sqlite3.DatabaseError as e:
            raise UnknownLayout(f"{path} is not a task's state file: {e}") from e
        if empty and app == 0:
            raise NoTask(f"{path} holds no task yet")
        _refuse_unknown(path, app, layout, empty)
        if layout < LAYOUT:
            raise UnknownLayout(
                f"{path} has layout {layout}, older than layout {LAYOUT} that this liulab-llms "
                "reads; open the task once for writing, with Task, to upgrade it"
            )

    def close(self) -> None:
        """Close the file and give up the hold, if this process has it."""
        with self._lock:
            self._conn.close()
            if self._holder is not None:
                self._holder.release()

    @contextlib.contextmanager
    def snapshot(self) -> Iterator[sqlite3.Connection]:
        """Run the reads inside it against one committed state, under the lock.

        A snapshot inside another joins it. On a read-only open the outermost one is a read
        transaction, which a write in progress keeps waiting up to `READ_WAIT_S`.

        Raises
        ------
        TaskBusy
            On a read-only open, a write in progress kept the read waiting too long.
        TaskNeedsRepair
            On a read-only open, a pass stopped in the middle of a write.
        """
        with self._lock:
            if not self.read_only or self._depth:
                self._depth += 1
                try:
                    yield self._conn
                finally:
                    self._depth -= 1
                return
            self._depth += 1
            try:
                try:
                    self._conn.execute("BEGIN")
                    yield self._conn
                finally:
                    with contextlib.suppress(sqlite3.Error):
                        self._conn.execute("ROLLBACK")
            except sqlite3.OperationalError as e:
                if e.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY:
                    raise TaskBusy(f"a write kept this read waiting {READ_WAIT_S:g} s") from e
                if e.sqlite_errorcode == sqlite3.SQLITE_READONLY_ROLLBACK:
                    raise TaskNeedsRepair(
                        "a pass stopped in the middle of a write, and a read-only open cannot "
                        "undo it; open the task once for writing, with Task, to repair it"
                    ) from e
                raise
            finally:
                self._depth -= 1

    @contextlib.contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One transaction, under the lock and only while this process holds the task."""
        if self._holder is None:
            raise TypeError("a task opened read-only cannot be written")
        with self._lock:
            self._holder.check()
            with self._conn:
                yield self._conn

    def start_pass(self, start: PassStart) -> int:
        """Record a new pass and return its number."""
        models = json.dumps({role: asdict(m) for role, m in start.models.items()})
        with self._write() as conn:
            cursor = conn.execute(
                "INSERT INTO passes (meter, kind, kind_version, label, models, ceiling_usd, "
                "off_peak_only, repo_version, labllm_version, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    start.meter,
                    start.kind,
                    start.kind_version,
                    start.label,
                    models,
                    start.ceiling_usd,
                    start.off_peak_only,
                    start.repo_version,
                    start.labllm_version,
                    start.note,
                ),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def end_pass(
        self,
        pass_id: int,
        *,
        stopped: str | None,
        spent_usd: float,
        first_cost_usd: float,
        requests: int,
        cache_hits: int,
    ) -> None:
        """Record how a pass ended."""
        with self._write() as conn:
            conn.execute(
                "UPDATE passes SET stopped=?, spent_usd=?, first_cost_usd=?, requests=?, "
                "cache_hits=? WHERE pass_id=?",
                (stopped, spent_usd, first_cost_usd, requests, cache_hits, pass_id),
            )

    def record(
        self,
        kind: str,
        key: str,
        pass_id: int,
        *,
        ending: str,
        inputs_tag: str | None,
        answer_json: str | None,
        reason: str | None,
        checks: Sequence[CheckResult],
        cost: UnitCost,
    ) -> None:
        """Record one unit's outcome in one pass, its checks and its cost, all at once."""
        with self._write() as conn:
            conn.execute(
                f"INSERT INTO outcomes ({_OUTCOME_FIELDS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    kind,
                    key,
                    pass_id,
                    ending,
                    inputs_tag,
                    answer_json,
                    reason,
                    cost.spent_usd,
                    cost.first_cost_usd,
                    cost.requests,
                    cost.cache_hits,
                    json.dumps(cost.usage),
                    cost.seconds,
                ),
            )
            conn.executemany(
                "INSERT INTO checks VALUES (?,?,?,?,?,?)",
                [(kind, key, pass_id, i, c.name, c.message) for i, c in enumerate(checks)],
            )

    def record_unstarted(
        self, kind: str, keys: Sequence[str], pass_id: int, *, reason: str
    ) -> None:
        """Record units a pass never started as unfinished for `reason`, all at once."""
        with self._write() as conn:
            conn.executemany(
                f"INSERT INTO outcomes ({_OUTCOME_FIELDS}) "
                "VALUES (?, ?, ?, 'unfinished', NULL, NULL, ?, 0, 0, 0, 0, '{}', NULL)",
                [(kind, key, pass_id, reason) for key in keys],
            )

    def outcomes(self, kind: str, key: str) -> list[Outcome]:
        """Return every pass's outcome for one unit, oldest first."""
        with self.snapshot() as conn:
            rows = conn.execute(
                f"SELECT {_OUTCOME_COLUMNS} FROM outcomes o JOIN passes p USING (pass_id) "
                "WHERE o.kind=? AND o.key=? ORDER BY o.pass_id",
                (kind, key),
            ).fetchall()
            return [self._outcome(row) for row in rows]

    def current(self, kind: str, key: str) -> Outcome | None:
        """Return the outcome of the latest pass that reached an ending for one unit, if any."""
        with self.snapshot() as conn:
            row = conn.execute(
                f"SELECT {_OUTCOME_COLUMNS} FROM outcomes o JOIN passes p USING (pass_id) "
                f"WHERE o.kind=? AND o.key=? AND o.ending IN {_ENDED} "
                "ORDER BY o.pass_id DESC LIMIT 1",
                (kind, key),
            ).fetchone()
            return None if row is None else self._outcome(row)

    def latest(self, kind: str) -> list[tuple[str, str, str | None]]:
        """Return each unit's key, ending and inputs tag in its latest ended pass, by key."""
        with self.snapshot() as conn:
            return conn.execute(
                "SELECT key, ending, inputs_tag FROM outcomes o "
                "WHERE kind=? AND pass_id = ("
                "  SELECT max(pass_id) FROM outcomes"
                f"  WHERE kind=o.kind AND key=o.key AND ending IN {_ENDED}"
                ") ORDER BY key",
                (kind,),
            ).fetchall()

    def keys(self, kind: str) -> list[str]:
        """Return every key with an outcome of one kind, in key order."""
        with self.snapshot() as conn:
            rows = conn.execute(
                "SELECT DISTINCT key FROM outcomes WHERE kind=? ORDER BY key", (kind,)
            ).fetchall()
        return [key for (key,) in rows]

    def passes(self, kind: str) -> list[Pass]:
        """Return every pass of one kind, oldest first."""
        with self.snapshot() as conn:
            rows = conn.execute(
                "SELECT pass_id, meter, kind, kind_version, label, note, models, ceiling_usd, "
                "off_peak_only, repo_version, labllm_version, spent_usd IS NOT NULL, stopped, "
                "coalesce(spent_usd, (SELECT total(spent_usd) FROM outcomes o "
                "  WHERE o.pass_id = p.pass_id)), "
                "coalesce(first_cost_usd, (SELECT total(first_cost_usd) FROM outcomes o "
                "  WHERE o.pass_id = p.pass_id)), "
                "coalesce(requests, (SELECT coalesce(sum(requests), 0) FROM outcomes o "
                "  WHERE o.pass_id = p.pass_id)), "
                "coalesce(cache_hits, (SELECT coalesce(sum(cache_hits), 0) FROM outcomes o "
                "  WHERE o.pass_id = p.pass_id)) "
                "FROM passes p WHERE kind=? ORDER BY pass_id",
                (kind,),
            ).fetchall()
            counted = conn.execute(
                "SELECT pass_id, ending, count(*) FROM outcomes WHERE kind=? "
                "GROUP BY pass_id, ending",
                (kind,),
            ).fetchall()
        counts: dict[int, dict[str, int]] = {
            row[0]: dict.fromkeys((*PASS_ENDINGS, *NOT_ENDINGS), 0) for row in rows
        }
        for pass_id, ending, n in counted:
            counts[pass_id][ending] = n
        passes = []
        for row in rows:
            (pass_id, meter, kind_name, kind_version, label, note, models) = row[:7]
            (ceiling_usd, off_peak_only, repo_version, labllm_version, ended, stopped) = row[7:13]
            (spent, first, requests, cache_hits) = row[13:]
            passes.append(
                Pass(
                    pass_id=pass_id,
                    meter=meter,
                    kind=kind_name,
                    kind_version=kind_version,
                    label=label,
                    note=note,
                    models=_models(models),
                    ceiling_usd=ceiling_usd,
                    off_peak_only=bool(off_peak_only),
                    repo_version=repo_version,
                    labllm_version=labllm_version,
                    ended=bool(ended),
                    stopped=stopped,
                    counts=counts[pass_id],
                    spent_usd=spent,
                    first_cost_usd=first,
                    requests=requests,
                    cache_hits=cache_hits,
                )
            )
        return passes

    def _outcome(self, row: tuple[Any, ...]) -> Outcome:
        (kind, key, pass_id, ending, inputs_tag, answer, reason) = row[:7]
        (spent, first, requests, cache_hits, usage, seconds) = row[7:13]
        (meter, kind_version, models, repo_version, labllm_version) = row[13:]
        checks = self._conn.execute(
            "SELECT name, message FROM checks WHERE kind=? AND key=? AND pass_id=? ORDER BY seq",
            (kind, key, pass_id),
        ).fetchall()
        stamp = Stamp(
            kind=kind,
            kind_version=kind_version,
            models=_models(models),
            inputs_tag=inputs_tag,
            pass_id=pass_id,
            meter=meter,
            repo_version=repo_version,
            labllm_version=labllm_version,
        )
        return Outcome(
            key=key,
            ending=ending,
            answer=None if answer is None else json.loads(answer),
            reason=reason,
            checks=tuple(CheckResult(name, message) for name, message in checks),
            stamp=stamp,
            cost=None
            if usage is None
            else UnitCost(spent, first, requests, cache_hits, json.loads(usage), seconds),
        )


def _connect_read_only(path: Path) -> sqlite3.Connection:
    """Open the file read-only, so SQLite creates no journal and touches no lock file."""
    if not path.is_file():
        raise NoTask(f"{path.parent} holds no task: there is no {path.name}")
    try:
        return sqlite3.connect(
            f"{path.absolute().as_uri()}?mode=ro",
            uri=True,
            check_same_thread=False,
            timeout=READ_WAIT_S,
            isolation_level=None,
        )
    except sqlite3.OperationalError as e:
        raise NoTask(f"{path.parent} holds no task: {e}") from e


def _layout(conn: sqlite3.Connection) -> tuple[int, int, bool]:
    """Return the file's application id, its layout, and whether it has no tables."""
    app = conn.execute("PRAGMA application_id").fetchone()[0]
    layout = conn.execute("PRAGMA user_version").fetchone()[0]
    empty = conn.execute("SELECT count(*) FROM sqlite_schema").fetchone()[0] == 0
    return app, layout, empty


def _refuse_unknown(path: Path, app: int, layout: int, empty: bool) -> None:
    if not (empty and app == 0) and app != _APPLICATION_ID:
        raise UnknownLayout(f"{path} is not a task's state file")
    if layout > LAYOUT:
        raise UnknownLayout(
            f"{path} has layout {layout}, newer than layout {LAYOUT} that this liulab-llms "
            "reads; upgrade liulab-llms to open it"
        )


def _models(text: str) -> dict[str, Model]:
    return {role: Model(**m) for role, m in json.loads(text).items()}


_OUTCOME_FIELDS = (
    "kind, key, pass_id, ending, inputs_tag, answer, reason, "
    "spent_usd, first_cost_usd, requests, cache_hits, usage, seconds"
)
_OUTCOME_COLUMNS = (
    "o.kind, o.key, o.pass_id, o.ending, o.inputs_tag, o.answer, o.reason, "
    "o.spent_usd, o.first_cost_usd, o.requests, o.cache_hits, o.usage, o.seconds, "
    "p.meter, p.kind_version, p.models, p.repo_version, p.labllm_version"
)
_ENDED = "(" + ", ".join(f"'{ending}'" for ending in ENDINGS) + ")"
