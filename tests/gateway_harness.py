"""The shared suite over the real gateway: its HTTP route, in this process, on a real PostgreSQL.

One throwaway PostgreSQL serves the whole session; each harness empties the record and starts a
fresh server, so in-memory state such as meters starts clean too.
"""

import atexit
import json
import threading
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from typing import Any

import psycopg

from harness import Harness
from labllm import Gateway
from labllm.client import HttpTransport
from labllm.server import Attempt, Prices, Provider, Record, Server
from labllm.server.throwaway import throwaway_database
from labllm.testing import Exchange, FakeClock, Price

_session = ExitStack()
_database: list[str] = []
_lock = threading.Lock()


def database() -> str:
    """The session's throwaway PostgreSQL, started on first use and stopped at exit."""
    with _lock:
        if not _database:
            _database.append(_session.enter_context(throwaway_database()))
            atexit.register(_session.close)
        return _database[0]


def empty(conninfo: str) -> None:
    """Empty the record, as if the gateway had never answered a call."""
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS exchanges")


@contextmanager
def running(server: Server, port: int = 0) -> Iterator[str]:
    """Serve `server` over HTTP on a local port, a free one by default, and yield its address."""
    httpd = server.http("127.0.0.1", port)
    # A short poll, so `shutdown` returns at once rather than after the default half second.
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def stand_in(fn: Any, prices: Mapping[str, Price]) -> Provider:
    """Adapt a suite provider function into the gateway's provider, by the fake's token rule."""

    def provider(form: dict[str, Any]) -> Attempt:
        out = fn(form)
        price = prices[form["model"]]
        if form["route"] == "chat":
            input_tokens = sum(len(m["content"]) for m in form["messages"])
            output_tokens = len(out)
        else:
            input_tokens = len(form["description"]) + len(json.dumps(form["questions"]))
            output_tokens = len(json.dumps(out))
        cost = input_tokens * price.input_usd_per_token + output_tokens * price.output_usd_per_token
        usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        return Attempt(output=out, usage=usage, cost_usd=cost)

    return provider


class GatewayHarness:
    """The shared suite over `labllm.server.Server`."""

    def __init__(self, address: str, record: Record, clock: FakeClock) -> None:
        self.fn: Any = _unset
        self._address = address
        self._record = record
        self._clock = clock

    def client(self, *, repo: str = "tests", person: str | None = None) -> Gateway:
        return Gateway(
            repo=repo, person=person, transport=HttpTransport(self._address), clock=self._clock
        )

    def provider(self, fn: Any) -> None:
        self.fn = fn

    def record(self) -> list[Exchange]:
        return read_record(self._record.conninfo)


def read_record(conninfo: str) -> list[Exchange]:
    """Every exchange in the record, oldest first, as the suite reads one."""
    with psycopg.connect(conninfo) as conn:
        rows = conn.execute(
            "SELECT key, cached, meter, label, ceiling_usd, off_peak_only, person, repo, tag,"
            " form, reply, error, cost_usd FROM exchanges ORDER BY id"
        ).fetchall()
    return [Exchange(*row) for row in rows]


@contextmanager
def gateway_harness(prices: Mapping[str, Price]) -> Iterator[Harness]:
    conninfo = database()
    empty(conninfo)
    clock = FakeClock()
    with Record(conninfo) as record:
        harness: list[GatewayHarness] = []
        provider = stand_in(lambda form: harness[0].fn(form), prices)
        server = Server(record, Prices(prices), {"chat": provider, "jev": provider}, clock=clock)
        with running(server) as address:
            harness.append(GatewayHarness(address, record, clock))
            yield harness[0]


def _unset(form: dict[str, Any]) -> Any:
    raise AssertionError("the test set no provider function")
