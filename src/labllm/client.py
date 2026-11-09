"""The client: `Gateway`, its meters, and the replies a call returns."""

import getpass
import http.client
import logging
import math
import os
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import urlsplit

from labllm import wire
from labllm.clock import Clock, SystemClock
from labllm.errors import GatewayUnreachable, PeakHours, Stopped
from labllm.model import Model

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatReply:
    """A chat call's answer. `text` is unvalidated: the caller decides what it means.

    `cost_usd` is what this call paid, zero on a cache hit; `first_cost_usd` is what the
    request cost when it was first answered.
    """

    text: str
    usage: dict[str, Any]
    cost_usd: float
    first_cost_usd: float
    cached: bool


@dataclass(frozen=True)
class JevReply:
    """A Jev call's answer. `answers` is the JSON exactly as Jev returned it."""

    answers: Any
    usage: dict[str, Any]
    cost_usd: float
    first_cost_usd: float
    cached: bool


class Transport(Protocol):
    """How a `Gateway` reaches its gateway. `labllm.testing.FakeGateway` is one."""

    def send(self, path: str, body: bytes) -> tuple[int, bytes]:
        """Post `body` to `path` and return the response's status and body.

        Raises
        ------
        ConnectionError
            The gateway refused the connection, or dropped it before answering.
        """
        ...


class HttpTransport:
    """HTTP to a gateway at a local `http://host:port` address, one connection per call."""

    def __init__(self, address: str) -> None:
        url = urlsplit(address)
        if url.scheme != "http" or not url.hostname or url.port is None:
            raise GatewayUnreachable(f"the gateway's address is http://host:port, not {address!r}")
        self._host, self._port, self._prefix = url.hostname, url.port, url.path.rstrip("/")

    def send(self, path: str, body: bytes) -> tuple[int, bytes]:
        """Post `body` to `path` and return the response's status and body."""
        # No timeout: a call may wait at the gateway for its turn or for off-peak hours.
        conn = http.client.HTTPConnection(self._host, self._port)
        try:
            conn.request("POST", self._prefix + path, body, {"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()


class Gateway:
    """The client of the lab gateway. One per process; safe from many threads.

    The gateway's address comes from `$LABLLM_GATEWAY`, a local address the caller's SSH
    tunnel serves. A refused connection is retried for `connect_retry_s` seconds, so a gateway
    restart does not stop the caller, and then raises `GatewayUnreachable`.

    Parameters
    ----------
    repo
        The calling repo's label, recorded with each call.
    person
        Who is calling, recorded with each call. Defaults to the login name.
    transport
        Replaces HTTP to `$LABLLM_GATEWAY`, as `labllm.testing.FakeGateway` does.
    clock
        Every wait goes through it.

    Raises
    ------
    GatewayUnreachable
        No transport is given and `$LABLLM_GATEWAY` is unset or not an `http://host:port`.
    """

    connect_retry_s = 60.0

    def __init__(
        self,
        *,
        repo: str,
        person: str | None = None,
        transport: Transport | None = None,
        clock: Clock | None = None,
    ) -> None:
        if transport is None:
            address = os.environ.get("LABLLM_GATEWAY")
            if not address:
                raise GatewayUnreachable(
                    "set LABLLM_GATEWAY to the local address of your tunnel to the gateway"
                )
            transport = HttpTransport(address)
        self.repo = repo
        self.person = person or getpass.getuser()
        self._transport = transport
        self._clock = clock or SystemClock()

    @property
    def clock(self) -> Clock:
        """The clock every wait goes through, and the one a caller times its calls by."""
        return self._clock

    def meter(self, label: str, *, ceiling_usd: float, off_peak_only: bool = False) -> "Meter":
        """Open a meter: a group of calls under one dollar ceiling.

        Parameters
        ----------
        label
            For people reading the gateway's record. Two meters never share a ceiling,
            whatever their labels.
        ceiling_usd
            Required. The gateway refuses a call whose largest possible cost would cross it.
        off_peak_only
            Send calls only in a provider's off-peak hours.

        Raises
        ------
        ValueError
            `ceiling_usd` is not a finite number of dollars, zero or more.
        """
        if (
            isinstance(ceiling_usd, bool)
            or not isinstance(ceiling_usd, int | float)
            or not math.isfinite(ceiling_usd)
            or ceiling_usd < 0
        ):
            raise ValueError(f"ceiling_usd must be a number of dollars, not {ceiling_usd!r}")
        return Meter(self, label, float(ceiling_usd), off_peak_only)

    def _send(self, path: str, body: bytes, wake: threading.Event) -> dict[str, Any] | None:
        """Return the reply, or None if `wake` ended a wait for off-peak hours."""
        deadline = self._clock.now() + self.connect_retry_s
        delay = 0.5
        while True:
            sent = self._clock.now()
            try:
                status, out = self._transport.send(path, body)
            except ConnectionError as e:
                left = deadline - self._clock.now()
                if left <= 0:
                    raise GatewayUnreachable(f"the gateway does not answer: {e}") from e
                self._clock.sleep(min(delay, left))
                delay = min(delay * 2, 8.0)
                continue
            try:
                return wire.decode_response(status, out)
            except PeakHours as e:
                log.info("%s; waiting", e)
                # A gateway clock a little behind this one must not cause a burst of resends.
                self._clock.sleep_until(max(e.until, sent + 1.0), wake)
                if wake.is_set():
                    return None
                deadline = self._clock.now() + self.connect_retry_s
                delay = 0.5


class Meter:
    """Labels, counts and limits a group of calls. Open one with `Gateway.meter`.

    The counters cover every reply so far: `spent_usd` paid now, `first_cost_usd` as first
    paid, `requests` answered, and how many of them were `cache_hits`. `id` is the meter's
    identity on the gateway.

    Every call raises a `Stopped` subclass when the caller's loop must stop, and `Rejected`
    when the request is a bug. An off-peak-only call that meets peak hours waits, holding no
    lock, until they end, then is sent again.
    """

    def __init__(self, gateway: Gateway, label: str, ceiling_usd: float, off_peak_only: bool):
        self.id = uuid.uuid4().hex
        self.label = label
        self.ceiling_usd = ceiling_usd
        self.off_peak_only = off_peak_only
        self.spent_usd = 0.0
        self.first_cost_usd = 0.0
        self.requests = 0
        self.cache_hits = 0
        self._gateway = gateway
        self._lock = threading.Lock()
        self._stopped: Stopped | None = None
        self._wake = threading.Event()

    def stop(self, error: Stopped) -> None:
        """Send no further call. Calls already sent finish.

        Each later call, and each one waiting out peak hours, raises `error`'s class with its
        message at once.
        """
        with self._lock:
            self._stopped = self._stopped or error
        self._wake.set()

    def __enter__(self) -> "Meter":
        """Return the meter."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Leave the meter; its counters stay readable."""
        return None

    def chat(
        self,
        model: Model,
        *,
        user: str,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        tag: str | None = None,
    ) -> ChatReply:
        """Send one chat request and wait for its reply.

        `schema` is a plain JSON Schema dictionary, sent and hashed exactly as written.
        `tag` joins the call's cost to the caller's own row; it never changes the cache key.
        """
        r = self._call(wire.chat_form(model, user=user, system=system, schema=schema), tag)
        return ChatReply(r["text"], r["usage"], r["cost_usd"], r["first_cost_usd"], r["cached"])

    def jev(
        self,
        model: Model,
        *,
        description: str,
        questions: Sequence[Any],
        tag: str | None = None,
    ) -> JevReply:
        """Ask Jev closed questions about `description` and wait for its answers."""
        form = wire.jev_form(model, description=description, questions=questions)
        r = self._call(form, tag)
        return JevReply(r["answers"], r["usage"], r["cost_usd"], r["first_cost_usd"], r["cached"])

    def _call(self, form: dict[str, Any], tag: str | None) -> dict[str, Any]:
        path = wire.PATHS[form["route"]]
        call = wire.Call(
            form=form,
            meter=self.id,
            label=self.label,
            ceiling_usd=self.ceiling_usd,
            off_peak_only=self.off_peak_only,
            person=self._gateway.person,
            repo=self._gateway.repo,
            tag=tag,
        )
        r = None if self._wake.is_set() else self._gateway._send(path, call.encode(), self._wake)
        if r is None:
            with self._lock:
                stopped = self._stopped
            assert stopped is not None
            raise type(stopped)(str(stopped))
        try:
            cost, first, cached = float(r["cost_usd"]), float(r["first_cost_usd"]), r["cached"]
        except (KeyError, TypeError, ValueError) as e:
            raise GatewayUnreachable(f"the gateway's reply lacks {e}") from e
        with self._lock:
            self.spent_usd += cost
            self.first_cost_usd += first
            self.requests += 1
            self.cache_hits += bool(cached)
        return r
