"""The gateway's server: answers each call from the cache or a provider, under its meter's ceiling."""

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

from labllm import wire
from labllm.clock import Clock, SystemClock
from labllm.errors import CeilingReached, Rejected, Stopped
from labllm.server.prices import Prices
from labllm.server.record import Record, Row

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Attempt:
    """What one attempt at a provider returned.

    `output` is the reply text for chat, or Jev's answers; `usage` is the provider's usage as
    received; `response` is the provider's whole response, kept in the record.
    """

    output: Any
    usage: dict[str, Any]
    cost_usd: float
    response: Any = None


#: Sends one request form to a provider, once. It raises a `Stopped` subclass or `Rejected`
#: for a failure the caller must see. The seam where tests play the provider.
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
        Times each exchange.
    """

    def __init__(
        self,
        record: Record,
        prices: Prices,
        providers: Mapping[str, Provider],
        *,
        clock: Clock | None = None,
    ) -> None:
        self._record = record
        self._prices = prices
        self._providers = dict(providers)
        self._clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._meters: dict[str, _Meter] = {}

    def answer(self, path: str, body: bytes) -> tuple[int, bytes]:
        """Answer one HTTP post with a status and body, as `labllm.wire` encodes them."""
        try:
            call = wire.Call.decode(body)
            _check(call.form)
            if wire.PATHS[call.form["route"]] != path:
                raise Rejected(f"{path} does not take a {call.form['route']} call")
            return wire.encode_reply(self._answer(call))
        except (Stopped, Rejected) as e:
            return wire.encode_error(e)
        except Exception as e:
            log.exception("the gateway failed on a call to %s", path)
            return wire.encode_error(Stopped(f"the gateway failed: {e!r}"))

    def http(self, host: str, port: int) -> ThreadingHTTPServer:
        """Return an HTTP server for this gateway at `host` and `port`, not yet serving."""
        httpd = _HTTPServer((host, port), _Handler)
        httpd.gateway = self
        return httpd

    def _answer(self, call: wire.Call) -> dict[str, Any]:
        form = call.form
        key = wire.cache_key(form)
        started = self._clock.now()
        entry = self._record.entry(key)
        if entry is not None:
            self._record.add(Row(call, key, started, self._clock.now(), cached=True))
            return {
                **entry.reply,
                "usage": entry.usage,
                "cost_usd": 0.0,
                "first_cost_usd": entry.cost_usd,
                "cached": True,
            }
        provider = self._providers.get(form["route"])
        if provider is None:
            raise Rejected(f"this gateway does not serve {form['route']} calls yet")
        reserve = self._prices.largest_cost(form)
        meter = self._hold(call, reserve)
        try:
            attempt = provider(form)
        except (Stopped, Rejected) as e:
            self._settle(meter, reserve, 0.0)
            self._record.add(
                Row(call, key, started, self._clock.now(), error=e.reason, message=str(e))
            )
            raise
        except Exception as e:
            self._settle(meter, reserve, 0.0)
            self._record.add(
                Row(call, key, started, self._clock.now(), error=Stopped.reason, message=repr(e))
            )
            raise
        self._settle(meter, reserve, attempt.cost_usd)
        reply = {"text" if form["route"] == "chat" else "answers": attempt.output}
        self._record.add(
            Row(
                call,
                key,
                started,
                self._clock.now(),
                reply=reply,
                usage=attempt.usage,
                response=attempt.response,
                cost_usd=attempt.cost_usd,
            )
        )
        return {
            **reply,
            "usage": attempt.usage,
            "cost_usd": attempt.cost_usd,
            "first_cost_usd": attempt.cost_usd,
            "cached": False,
        }

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
