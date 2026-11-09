"""The record: one PostgreSQL table of every exchange, which is also the response cache."""

from dataclasses import dataclass
from types import TracebackType
from typing import Any

from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from labllm import wire

#: Run in order at every start; each statement is idempotent, so a later column is added with
#: `ADD COLUMN IF NOT EXISTS` at the end. `json`, not `jsonb`: it keeps a body exactly as sent,
#: key order included.
SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS exchanges (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        started_at timestamptz NOT NULL,
        finished_at timestamptz NOT NULL,
        key text NOT NULL,
        route text NOT NULL,
        provider text NOT NULL,
        model text NOT NULL,
        cached boolean NOT NULL,
        form json,
        reply json,
        usage json,
        response json,
        error text,
        message text,
        cost_usd double precision NOT NULL,
        meter text NOT NULL,
        label text NOT NULL,
        ceiling_usd double precision NOT NULL,
        off_peak_only boolean NOT NULL,
        person text NOT NULL,
        repo text NOT NULL,
        tag text,
        retired_at timestamptz
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS exchanges_entry ON exchanges (key, id)
        WHERE NOT cached AND error IS NULL AND retired_at IS NULL
    """,
    "CREATE INDEX IF NOT EXISTS exchanges_meter ON exchanges (meter)",
    "ALTER TABLE exchanges ADD COLUMN IF NOT EXISTS attempt integer",
    "ALTER TABLE exchanges ADD COLUMN IF NOT EXISTS waited_s double precision",
    "ALTER TABLE exchanges ALTER COLUMN finished_at DROP NOT NULL",
    "ALTER TABLE exchanges ADD COLUMN IF NOT EXISTS reserved_usd double precision",
)


@dataclass(frozen=True)
class Entry:
    """A cache entry: what the first successful attempt at a key returned and cost."""

    reply: dict[str, Any]
    usage: dict[str, Any]
    cost_usd: float


@dataclass(frozen=True)
class Row:
    """One exchange to add. A hit has `cached` and no bodies; a failure has `error`.

    `attempt` numbers a call's attempts from 1, and `waited_s` is how long this one waited
    for its turn or the provider's pause; a hit has neither. `reserved_usd` is the attempt's
    reservation, which counts as its cost until it finishes.
    """

    call: wire.Call
    key: str
    started_at: float
    finished_at: float | None
    cached: bool = False
    reply: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    response: Any = None
    error: str | None = None
    message: str | None = None
    cost_usd: float = 0.0
    attempt: int | None = None
    waited_s: float | None = None
    reserved_usd: float | None = None


class Record:
    """The gateway's record in the PostgreSQL database at `conninfo`; creates its table.

    An attempt's row is added when it starts and finished when it ends, so an attempt cut
    short by a crash keeps a row with no `finished_at`. The cache entry for a key is its first
    finished successful attempt not retired by hand. Retiring sets `retired_at` and keeps the
    row.
    """

    def __init__(self, conninfo: str, *, connections: int = 10) -> None:
        self.conninfo = conninfo
        self._pool = ConnectionPool(
            conninfo,
            min_size=1,
            max_size=connections,
            kwargs={"autocommit": True},
            check=ConnectionPool.check_connection,
            open=True,
        )
        with self._pool.connection() as conn:
            for statement in SCHEMA:
                conn.execute(statement)

    def __enter__(self) -> "Record":
        """Return the record."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close its connections."""
        self.close()

    def close(self) -> None:
        """Close its connections."""
        self._pool.close()

    def entry(self, key: str) -> Entry | None:
        """Return the cache entry for `key`, or None."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT reply, usage, cost_usd FROM exchanges"
                " WHERE key = %s AND NOT cached AND error IS NULL AND retired_at IS NULL"
                " AND finished_at IS NOT NULL ORDER BY id LIMIT 1",
                (key,),
            ).fetchone()
        return None if row is None else Entry(*row)

    def spent(self, meter: str) -> float:
        """Return what the record says `meter` has paid, an unfinished attempt at its reservation."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT coalesce(sum(CASE WHEN finished_at IS NULL"
                " THEN coalesce(reserved_usd, 0) ELSE cost_usd END), 0)"
                " FROM exchanges WHERE meter = %s",
                (meter,),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def add(self, row: Row) -> int:
        """Add one exchange, finished or not, and return its id."""
        call, form = row.call, row.call.form
        values = {
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "key": row.key,
            "route": form["route"],
            "provider": form["provider"],
            "model": form["model"],
            "cached": row.cached,
            "form": None if row.cached else Json(form),
            "reply": _json(row.reply),
            "usage": _json(row.usage),
            "response": _json(row.response),
            "error": row.error,
            "message": row.message,
            "cost_usd": row.cost_usd,
            "meter": call.meter,
            "label": call.label,
            "ceiling_usd": call.ceiling_usd,
            "off_peak_only": call.off_peak_only,
            "person": call.person,
            "repo": call.repo,
            "tag": call.tag,
            "attempt": row.attempt,
            "waited_s": row.waited_s,
            "reserved_usd": row.reserved_usd,
        }
        with self._pool.connection() as conn:
            added = conn.execute(_INSERT, values).fetchone()
        assert added is not None
        return int(added[0])

    def finish(self, id: int, row: Row) -> None:
        """Finish the exchange `id` with `row`'s end, bodies, error and cost."""
        values = {
            "id": id,
            "finished_at": row.finished_at,
            "reply": _json(row.reply),
            "usage": _json(row.usage),
            "response": _json(row.response),
            "error": row.error,
            "message": row.message,
            "cost_usd": row.cost_usd,
        }
        with self._pool.connection() as conn:
            conn.execute(_FINISH, values)


_COLUMNS = (
    "key, route, provider, model, cached, form, reply, usage, response, error, message,"
    " cost_usd, meter, label, ceiling_usd, off_peak_only, person, repo, tag, attempt, waited_s,"
    " reserved_usd"
)
_INSERT = (
    "INSERT INTO exchanges (started_at, finished_at, "
    + _COLUMNS
    + ") VALUES (to_timestamp(%(started_at)s), to_timestamp(%(finished_at)s),"
    " %(key)s, %(route)s, %(provider)s, %(model)s, %(cached)s, %(form)s, %(reply)s, %(usage)s,"
    " %(response)s, %(error)s, %(message)s, %(cost_usd)s, %(meter)s, %(label)s,"
    " %(ceiling_usd)s, %(off_peak_only)s, %(person)s, %(repo)s, %(tag)s, %(attempt)s,"
    " %(waited_s)s, %(reserved_usd)s) RETURNING id"
)
_FINISH = (
    "UPDATE exchanges SET finished_at = to_timestamp(%(finished_at)s), reply = %(reply)s,"
    " usage = %(usage)s, response = %(response)s, error = %(error)s, message = %(message)s,"
    " cost_usd = %(cost_usd)s WHERE id = %(id)s"
)


def _json(value: Any) -> Json | None:
    return None if value is None else Json(value)
