"""The client: `Gateway`, its meters, the replies a call returns, and agents' tools."""

import getpass
import http.client
import json
import logging
import math
import os
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import urlsplit

from labllm import wire
from labllm.clock import Clock, SystemClock
from labllm.errors import (
    GatewayUnreachable,
    PeakHours,
    Rejected,
    StepLimitReached,
    Stopped,
    UnknownTool,
)
from labllm.model import Model

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatReply:
    """A chat call's answer. `text` is unvalidated: the caller decides what it means.

    For a call sent with no schema, `reply_json` reads the JSON value out of `text`.

    `cost_usd` is what this call paid, failed attempts included, zero on a cache hit;
    `first_cost_usd` is what the answer cost when it was first paid. `attempts` counts the
    requests the gateway sent the provider for this call, retries included: zero on a cache
    hit.
    """

    text: str
    usage: dict[str, Any]
    cost_usd: float
    first_cost_usd: float
    cached: bool
    attempts: int = 1


@dataclass(frozen=True)
class Tool:
    """A function the model may ask for in `Meter.agent`, run in the caller's own process.

    `name`, `description` and `parameters`, a JSON Schema of the arguments, are sent to the
    model and enter the request form; `run` never leaves the process. It takes the arguments
    the model wrote, parsed from JSON, and returns the result the model is sent: a string as
    it is, anything else as JSON. DeepSeek takes `parameters` in its strict mode, where every
    object lists all its properties as required and sets `additionalProperties` to false.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class AgentReply:
    """An agent's final answer. `text` is unvalidated, as `ChatReply.text` is.

    `steps` counts its chat calls, the last included; `cost_usd` is what they paid, zero for
    each cache hit.
    """

    text: str
    steps: int
    cost_usd: float


@dataclass(frozen=True)
class JevReply:
    """A Jev call's answer. `answers` is the JSON exactly as Jev returned it.

    From the real gateway it is the ``answers`` object of Jev's response, keyed by question
    name: a question Jev left unanswered is absent. The other fields are `ChatReply`'s.
    """

    answers: Any
    usage: dict[str, Any]
    cost_usd: float
    first_cost_usd: float
    cached: bool
    attempts: int = 1


class Transport(Protocol):
    """How a `Gateway` reaches its gateway. `labllm.testing.FakeGateway` is one."""

    def send(self, path: str, body: bytes) -> tuple[int, bytes]:
        """Post `body` to `path` and return the response's status and body.

        Raises
        ------
        ConnectionError
            The gateway refused the connection, or it dropped with no reply.
        OSError, http.client.HTTPException
            The connection failed in any other way.
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
    tunnel serves. A refused connection, or one dropped with no reply, as a tunnel drops it
    while the gateway restarts, is sent again for `connect_retry_s` seconds, then raises
    `GatewayUnreachable`. A resend never pays twice: the gateway serves one whose call it
    already answered from the cache, and makes one whose call is in flight wait for it. Its
    counters then show no cost for that call; the gateway's record and ceiling hold it.

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

    Examples
    --------
    Through a tunnel to the lab gateway, so the doctest run skips each line that needs it:

    >>> from labllm import Gateway, Model
    >>> gw = Gateway(repo="my-repo")  # doctest: +SKIP
    >>> model = Model("deepseek", "deepseek-v4-flash", thinking=False, max_output_tokens=16)
    >>> with gw.meter("first try", ceiling_usd=0.01) as m:  # doctest: +SKIP
    ...     reply = m.chat(model, user="Reply with the single word OK.")
    >>> reply.text  # doctest: +SKIP
    'OK'
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
            Send calls only outside the peak hours in the model's price table entry. In peak
            a call waits until they end; a model with no off-peak hours is unaffected.

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

    def _send(
        self, path: str, body: bytes, wake: threading.Event, paid: Callable[[float, int], None]
    ) -> dict[str, Any] | None:
        """Return the reply, or None if `wake` ended a wait for off-peak hours.

        A peak reply's cost and attempts go to `paid` before the wait.
        """
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
            except (OSError, http.client.HTTPException) as e:
                raise GatewayUnreachable(f"the connection to the gateway failed: {e!r}") from e
            try:
                return wire.decode_response(status, out)
            except PeakHours as e:
                paid(e.cost_usd, e.attempts)
                log.info("%s; waiting", e)
                # A gateway clock a little behind this one must not cause a burst of resends.
                self._clock.sleep_until(max(e.until, sent + 1.0), wake)
                if wake.is_set():
                    return None
                deadline = self._clock.now() + self.connect_retry_s
                delay = 0.5


class Meter:
    """Labels, counts and limits a group of calls. Open one with `Gateway.meter`.

    The counters cover every call so far: `spent_usd` paid now, failed calls included;
    `first_cost_usd`, the answers as first paid; `requests` the gateway sent providers, retries
    included; and `cache_hits`, the calls the cache answered, which send none. `id` is the
    meter's identity on the gateway.

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
        self._stopped: Stopped | Rejected | None = None
        self._wake = threading.Event()

    def stop(self, error: Stopped | Rejected) -> None:
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
        user: str | list[dict[str, Any]],
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        tag: str | None = None,
        replicate: int = 1,
    ) -> ChatReply:
        """Send one chat request and wait for its reply.

        `user` is text or OpenAI content blocks, including an inline image, or a file for
        DeepSeek from `labllm.files.file_part`. `schema` is a plain JSON Schema dictionary,
        sent and hashed exactly as written. DeepSeek receives it as a strict function call;
        other providers receive native schema output.
        `tag` joins the call's cost to the caller's own row; it never changes the cache key.
        `replicate` asks for a deliberate repeat: replicate 2 of a request is a fresh answer,
        cached under its own key, and replicate 1 is the plain request.
        """
        form = wire.chat_form(model, user=user, system=system, schema=schema, replicate=replicate)
        r = self._call(form, tag)
        return ChatReply(
            r["text"], r["usage"], r["cost_usd"], r["first_cost_usd"], r["cached"], r["attempts"]
        )

    def agent(
        self,
        model: Model,
        *,
        user: str | list[dict[str, Any]],
        tools: Sequence[Tool],
        max_steps: int,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        tag: str | None = None,
    ) -> AgentReply:
        """Run an agent: chat calls in a loop that runs each tool the model asks for.

        Each step is one chat call, cached as any other, sent with the agent's own messages
        so far: the first call's, then each turn that asked for tools and each tool's result.
        The loop ends when the model answers without asking for a tool. `user`, `system`,
        `schema` and `tag` are `chat`'s. Agents may run from many threads on one meter, each
        with its own messages, and share its ceiling.

        Raises
        ------
        StepLimitReached
            The model still asked for tools at step `max_steps`.
        UnknownTool
            The model asked for a tool not in `tools`.
        GatewayUnreachable
            A gateway older than agents answered a step, now or earlier from its record.
        ValueError
            `max_steps` is not a whole number from 1, two tools share a name, or the model's
            arguments for a tool are not JSON.
        TypeError
            A tool's `run` returned something that is neither a string nor JSON.

        Every error `chat` raises, and any error a tool's `run` raises, comes out unchanged,
        and the agent ends there.
        """
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError(f"max_steps must be a whole number from 1, not {max_steps!r}")
        by_name = {t.name: t for t in tools}
        if len(by_name) != len(tools):
            raise ValueError("two tools share a name")
        definitions = [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in tools
        ]
        form = wire.chat_form(model, user=user, system=system, schema=schema, tools=definitions)
        messages: list[dict[str, Any]] = list(form["messages"])
        cost, step = 0.0, 0
        while True:
            step += 1
            r = self._call({**form, "messages": list(messages)}, tag)
            cost += float(r["cost_usd"])
            calls = r.get("tool_calls")
            if calls is None and definitions:
                raise GatewayUnreachable(
                    "the gateway's reply lacks tool_calls: a gateway older than agents answered "
                    "this step without its tools"
                )
            if not calls:
                return AgentReply(r["text"], step, cost)
            unknown = [c["name"] for c in calls if c["name"] not in by_name]
            if unknown:
                raise UnknownTool(
                    f"the model asked for {', '.join(unknown)}, which the agent was not given"
                )
            if step == max_steps:
                raise StepLimitReached(
                    f"the agent reached its limit of {max_steps} steps, and the model still "
                    f"asked for tools: {', '.join(c['name'] for c in calls)}"
                )
            results = []
            for c in calls:
                result = by_name[c["name"]].run(json.loads(c["arguments"] or "{}"))
                results.append(
                    result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                )
            messages += wire.tool_turn(r["text"], calls, results)

    def jev(
        self,
        model: Model,
        *,
        description: str,
        questions: Mapping[str, Any],
        tag: str | None = None,
    ) -> JevReply:
        """Ask Jev closed questions about `description` and wait for its answers.

        `questions` maps each name to a question as Jev's API takes it, such as
        ``{"type": "noul", "instructions": "Is it human?"}``; Jev answers under the same names.
        """
        form = wire.jev_form(model, description=description, questions=questions)
        r = self._call(form, tag)
        return JevReply(
            r["answers"], r["usage"], r["cost_usd"], r["first_cost_usd"], r["cached"], r["attempts"]
        )

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
        try:
            r = (
                None
                if self._wake.is_set()
                else self._gateway._send(path, call.encode(), self._wake, self._paid)
            )
        except (Stopped, Rejected) as e:
            self._paid(e.cost_usd, e.attempts)
            raise
        if r is None:
            with self._lock:
                stopped = self._stopped
            assert stopped is not None
            raise type(stopped)(str(stopped))
        try:
            cost, first, cached = float(r["cost_usd"]), float(r["first_cost_usd"]), r["cached"]
            # A reply with no count of attempts had one, or none for a hit.
            r["attempts"] = int(r.get("attempts", 0 if cached else 1))
        except (KeyError, TypeError, ValueError) as e:
            raise GatewayUnreachable(f"the gateway's reply lacks {e}") from e
        with self._lock:
            self.spent_usd += cost
            self.first_cost_usd += first
            self.requests += r["attempts"]
            self.cache_hits += bool(cached)
        return r

    def _paid(self, cost_usd: float, attempts: int) -> None:
        """Count what a call that brought no answer paid and sent."""
        with self._lock:
            self.spent_usd += cost_usd
            self.requests += attempts
