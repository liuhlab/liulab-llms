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

#: What a unit's outcome in one pass can be. The first four are endings; the last two are not.
ENDINGS = ("settled", "unsettled", "refused", "dropped")
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
)

#: The layout this version of liulab-llms writes.
LAYOUT = len(_UPGRADES)


#: How often a holder touches its holder file, and how long it may go untouched before a
#: process on another host counts the holder as gone.
HEARTBEAT_S = 20.0
STALE_S = 120.0


class UnknownLayout(Exception):  # noqa: N818 - the name callers catch
    """The state file is not one this version of liulab-llms can read."""


class TaskInUse(Exception):  # noqa: N818 - the name callers catch
    """Another process holds the task, or has taken it over from this one."""


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
    the same replies cost when first paid. `requests` counts every call the unit sent,
    answered or not, with the gateway's retries of each answered one, and `cache_hits` those
    the cache answered. `usage` sums the numbers in the replies' usage, key by key, replays
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


class StateFile:
    """One task's SQLite file, held by this process. Every method is safe from many threads.

    Raises
    ------
    TaskInUse
        Another process holds the task.
    UnknownLayout
        The file is not one this version of liulab-llms can read.
    """

    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._holder = Holder(path.with_suffix(".holder"), path.parent)
        try:
            self._conn = sqlite3.connect(path, check_same_thread=False)
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
            app = conn.execute("PRAGMA application_id").fetchone()[0]
            layout = conn.execute("PRAGMA user_version").fetchone()[0]
            empty = conn.execute("SELECT count(*) FROM sqlite_schema").fetchone()[0] == 0
        except sqlite3.DatabaseError as e:
            raise UnknownLayout(f"{path} is not a task's state file: {e}") from e
        if not (empty and app == 0) and app != _APPLICATION_ID:
            raise UnknownLayout(f"{path} is not a task's state file")
        if layout > LAYOUT:
            raise UnknownLayout(
                f"{path} has layout {layout}, newer than layout {LAYOUT} that this liulab-llms "
                "reads; upgrade liulab-llms to open it"
            )
        if layout < LAYOUT:
            steps = "".join(_UPGRADES[layout:])
            conn.executescript(
                f"BEGIN; {steps}; PRAGMA application_id={_APPLICATION_ID}; "
                f"PRAGMA user_version={LAYOUT}; COMMIT;"
            )
        conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        """Close the file and give up the hold."""
        with self._lock:
            self._conn.close()
            self._holder.release()

    @contextlib.contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One transaction, under the lock and only while this process holds the task."""
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
                "off_peak_only, repo_version, labllm_version) VALUES (?,?,?,?,?,?,?,?,?)",
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
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_OUTCOME_COLUMNS} FROM outcomes o JOIN passes p USING (pass_id) "
                "WHERE o.kind=? AND o.key=? ORDER BY o.pass_id",
                (kind, key),
            ).fetchall()
            return [self._outcome(row) for row in rows]

    def current(self, kind: str, key: str) -> Outcome | None:
        """Return the outcome of the latest pass that reached an ending for one unit, if any."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_OUTCOME_COLUMNS} FROM outcomes o JOIN passes p USING (pass_id) "
                f"WHERE o.kind=? AND o.key=? AND o.ending IN {_ENDED} "
                "ORDER BY o.pass_id DESC LIMIT 1",
                (kind, key),
            ).fetchone()
            return None if row is None else self._outcome(row)

    def latest(self, kind: str) -> list[tuple[str, str, str | None]]:
        """Return each unit's key, ending and inputs tag in its latest ended pass, by key."""
        with self._lock:
            return self._conn.execute(
                "SELECT key, ending, inputs_tag FROM outcomes o "
                "WHERE kind=? AND pass_id = ("
                "  SELECT max(pass_id) FROM outcomes"
                f"  WHERE kind=o.kind AND key=o.key AND ending IN {_ENDED}"
                ") ORDER BY key",
                (kind,),
            ).fetchall()

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
            models={role: Model(**m) for role, m in json.loads(models).items()},
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


_OUTCOME_FIELDS = (
    "kind, key, pass_id, ending, inputs_tag, answer, reason, "
    "spent_usd, first_cost_usd, requests, cache_hits, usage, seconds"
)
_OUTCOME_COLUMNS = (
    "o.kind, o.key, o.pass_id, o.ending, o.inputs_tag, o.answer, o.reason, "
    "o.spent_usd, o.first_cost_usd, o.requests, o.cache_hits, o.usage, o.seconds, "
    "p.meter, p.kind_version, p.models, p.repo_version, p.labllm_version"
)
_ENDED = "('settled', 'unsettled', 'refused', 'dropped')"
