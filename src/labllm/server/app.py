"""The gateway's server: answers each call from the cache or a provider, under its meter's ceiling."""

import dataclasses
import itertools
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

from labllm import peak, wire
from labllm.clock import Clock, SystemClock
from labllm.errors import (
    CeilingReached,
    GatewayUnreachable,
    PeakHours,
    ProviderUnavailable,
    Rejected,
    Stopped,
)
from labllm.flights import Flights
from labllm.retry import BUDGET_S, Retries, unreadable
from labllm.server.limits import Gate, Pause, RateLimit, estimate_input_tokens
from labllm.server.prices import Prices
from labllm.server.record import Record, Row

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Attempt:
    """What one attempt at a provider returned.

    `output` is the reply text for chat, or Jev's answers; `usage` is the provider's usage as
    received; `response` is the provider's whole response, kept in the record. `input_tokens`
    is the input the provider counted, if known; it corrects the rate limit's estimate.
    An empty `output` is a failed attempt, paid for and tried again.
    """

    output: Any
    usage: dict[str, Any]
    cost_usd: float
    response: Any = None
    input_tokens: int | None = None


def retry_after_s(headers: Mapping[str, str] | None) -> float | None:
    """Return the wait a provider's response headers ask for, in seconds, or None.

    Reads ``retry-after-ms``, then ``Retry-After`` in seconds or as an HTTP date.
    """
    if headers is None:
        return None
    for name, scale in (("retry-after-ms", 1e-3), ("retry-after", 1.0)):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            wait = float(raw) * scale
        except ValueError:
            try:
                wait = parsedate_to_datetime(raw).timestamp() - time.time()
            except (TypeError, ValueError):
                continue
        if math.isfinite(wait):
            return max(wait, 0.0)
    return None


#: Sends one request form to a provider, once. It raises `ProviderUnavailable` for a failure
#: worth another attempt, with the provider's overload shape; another `Stopped` subclass or
#: `Rejected` for one the caller must see at once. The seam where tests play the provider.
Provider = Callable[[dict[str, Any]], Attempt]


@dataclass
class _Meter:
    spent: float
    held: float = 0.0


class Server:
    """The lab gateway, minus its HTTP: one per process, safe from many threads.

    Parameters
    ----------
    record
        Every exchange goes in it, and the cache is read from it.
    prices
        Prices each call before it is sent.
    providers
        The provider for each route it serves, `"chat"` or `"jev"`.
    clock
        Times each exchange, decides peak hours, and runs every wait.
    peak_margin_s
        New off-peak-only calls stop this many seconds before peak starts.
    limits
        Rate limits by provider, such as ``"deepseek"``, or by one model, such as
        ``"openai/gpt-5.5"``. A model's own limits replace its provider's.
    retry_budget_s
        How long a call keeps trying an unavailable provider after its first failure.
    """

    def __init__(
        self,
        record: Record,
        prices: Prices,
        providers: Mapping[str, Provider],
        *,
        clock: Clock | None = None,
        peak_margin_s: float = peak.MARGIN_S,
        limits: Mapping[str, RateLimit] | None = None,
        retry_budget_s: float = BUDGET_S,
    ) -> None:
        self._record = record
        self._prices = prices
        self._providers = dict(providers)
        self._clock = clock or SystemClock()
        self._peak_margin_s = peak_margin_s
        self._limits = dict(limits or {})
        self._retry_budget_s = retry_budget_s
        self._lock = threading.Lock()
        self._meters: dict[str, _Meter] = {}
        self._gates: dict[str, Gate] = {}
        self._pauses: dict[str, Pause] = {}
        self._arrivals = itertools.count()
        self._flights = Flights(self._clock)

    def answer(self, path: str, body: bytes) -> tuple[int, bytes]:
        """Answer one HTTP post with a status and body, as `labllm.wire` encodes them."""
        try:
            call = wire.Call.decode(body)
            _check(call.form)
            if wire.PATHS[call.form["route"]] != path:
                raise Rejected(f"{path} does not take a {call.form['route']} call")
            return wire.encode_reply(self._answer(call))
        except (Stopped, Rejected, PeakHours) as e:
            return wire.encode_error(e)
        except Exception as e:
            log.exception("the gateway failed on a call to %s", path)
            return wire.encode_error(GatewayUnreachable(f"the gateway failed: {e!r}"))

    def http(self, host: str, port: int) -> ThreadingHTTPServer:
        """Return an HTTP server for this gateway at `host` and `port`, not yet serving."""
        httpd = _HTTPServer((host, port), _Handler)
        httpd.gateway = self
        return httpd

    def _answer(self, call: wire.Call) -> dict[str, Any]:
        form = call.form
        key = wire.cache_key(form)
        started = self._clock.now()
        hit = self._hit(call, key, started)
        if hit is not None:
            return hit
        provider = self._providers.get(form["route"])
        if provider is None:
            raise Rejected(f"this gateway does not serve {form['route']} calls yet")
        with self._flights.claim(key):
            hit = self._hit(call, key, started)
            if hit is not None:
                return hit
            return self._attempts(call, key, started, provider)

    def _hit(self, call: wire.Call, key: str, started: float) -> dict[str, Any] | None:
        entry = self._record.entry(key)
        if entry is None:
            return None
        self._record.add(Row(call, key, started, self._clock.now(), cached=True))
        return {
            **entry.reply,
            "usage": entry.usage,
            "cost_usd": 0.0,
            "first_cost_usd": entry.cost_usd,
            "cached": True,
            "attempts": 0,
        }

    def _attempts(
        self, call: wire.Call, key: str, started: float, provider: Provider
    ) -> dict[str, Any]:
        form = call.form
        reserve = self._prices.largest_cost(form)
        gate, pause = self._gate(form)
        tokens = estimate_input_tokens(form)
        arrival = next(self._arrivals)
        retries = Retries(self._clock, self._retry_budget_s)
        paid = 0.0
        try:
            while True:
                turn = gate.wait(arrival, tokens)
                try:
                    if call.off_peak_only:
                        off_peak = self._prices.price(form).off_peak
                        peak.check(off_peak, self._clock.now(), self._peak_margin_s)
                    meter = self._hold(call, reserve)
                except BaseException:
                    turn.cancel()
                    raise
                retries.attempts += 1
                sent = self._clock.now()
                done = Row(call, key, sent, sent, attempt=retries.attempts, waited_s=sent - started)
                input_tokens = None
                try:
                    attempt = provider(form)
                    input_tokens = attempt.input_tokens
                except Exception as e:
                    failure = unreadable(e, form["provider"])
                    self._settle(meter, reserve, 0.0)
                    self._record.add(self._ended(done, error=failure.reason, message=str(failure)))
                    if not isinstance(failure, ProviderUnavailable):
                        raise
                    # Overload or no answer at all: every caller of this provider waits it out.
                    pause.extend(until := retries.next_try(failure))
                    log.info(
                        "pausing %s until %.1f s from now: %s",
                        form["provider"],
                        until - sent,
                        failure,
                    )
                    again = retries.again(failure)
                else:
                    self._settle(meter, reserve, attempt.cost_usd)
                    reply = {"text" if form["route"] == "chat" else "answers": attempt.output}
                    if attempt.output:
                        self._record.add(
                            self._ended(
                                done,
                                reply=reply,
                                usage=attempt.usage,
                                response=attempt.response,
                                cost_usd=attempt.cost_usd,
                            )
                        )
                        return {
                            **reply,
                            "usage": attempt.usage,
                            "cost_usd": paid + attempt.cost_usd,
                            "first_cost_usd": attempt.cost_usd,
                            "cached": False,
                            "attempts": retries.attempts,
                        }
                    paid += attempt.cost_usd
                    empty = ProviderUnavailable(
                        f"{form['provider']} sent an empty reply", status=200
                    )
                    self._record.add(
                        self._ended(
                            done,
                            error=empty.reason,
                            message=str(empty),
                            usage=attempt.usage,
                            response=attempt.response,
                            cost_usd=attempt.cost_usd,
                        )
                    )
                    again = retries.again(empty)
                finally:
                    turn.done(input_tokens)
                started = self._clock.now()
                self._clock.sleep_until(again)
        except (Stopped, Rejected, PeakHours) as e:
            e.cost_usd, e.attempts = paid, retries.attempts
            raise

    def _ended(self, row: Row, **fields: Any) -> Row:
        return dataclasses.replace(row, finished_at=self._clock.now(), **fields)

    def _gate(self, form: dict[str, Any]) -> tuple[Gate, Pause]:
        provider = form["provider"]
        name = f"{provider}/{form['model']}"
        if name not in self._limits:
            name = provider
        with self._lock:
            pause = self._pauses.setdefault(provider, Pause())
            gate = self._gates.get(name)
            if gate is None:
                limit = self._limits.get(name, RateLimit())
                gate = self._gates[name] = Gate(limit, pause, self._clock)
            return gate, pause

    def _hold(self, call: wire.Call, reserve: float) -> _Meter:
        with self._lock:
            meter = self._meters.get(call.meter)
            if meter is None:
                # First sight since the start: what the meter paid before a restart counts.
                meter = self._meters[call.meter] = _Meter(self._record.spent(call.meter))
            held = meter.spent + meter.held
            if held + reserve > call.ceiling_usd:
                raise CeilingReached(
                    f"meter {call.label!r} has {held:.6f} USD spent or held, and this call may "
                    f"cost {reserve:.6f} more, over its ceiling of {call.ceiling_usd:.6f}"
                )
            meter.held += reserve
            return meter

    def _settle(self, meter: _Meter, reserve: float, cost: float) -> None:
        with self._lock:
            meter.held -= reserve
            meter.spent += cost


def _check(form: dict[str, Any]) -> None:
    settings = form.get("settings")
    if (
        not isinstance(form.get("provider"), str)
        or not isinstance(form.get("model"), str)
        or not isinstance(settings, dict)
        or not isinstance(settings.get("max_output_tokens"), int)
    ):
        raise Rejected("not a call: its form lacks a provider, a model or an output limit")


class _HTTPServer(ThreadingHTTPServer):
    gateway: Server


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        status, out = cast(_HTTPServer, self.server).gateway.answer(self.path, body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, format: str, *args: Any) -> None:
        log.info("%s %s", self.address_string(), format % args)
