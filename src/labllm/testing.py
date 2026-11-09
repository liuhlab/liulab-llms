"""A fake gateway and a fake clock, so tests of code that calls models run offline and free."""

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from labllm import peak, wire
from labllm.ceiling import Ceilings, largest_cost
from labllm.client import Gateway
from labllm.errors import PeakHours, ProviderUnavailable, Rejected, Stopped
from labllm.flights import Flights
from labllm.model import Price
from labllm.peak import OffPeak
from labllm.retry import Retries, unreadable

__all__ = ["Exchange", "FakeClock", "FakeGateway", "OffPeak", "Price", "Provider"]

#: Plays the provider: takes a request form and returns the reply text (chat) or Jev's
#: answers. It may raise one of the four stops or `Rejected`, as a provider would; any other
#: exception plays a response the gateway cannot read.
Provider = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class Exchange:
    """One attempt in the gateway's record, as a test reads it.

    A cache hit is an exchange with no bodies: `form` and `reply` are None and `cost_usd` is
    zero. A failed attempt has `error`, the reason of the error raised.
    """

    key: str
    cached: bool
    meter: str
    label: str
    ceiling_usd: float
    off_peak_only: bool
    person: str
    repo: str
    tag: str | None
    form: dict[str, Any] | None
    reply: dict[str, Any] | None
    error: str | None
    cost_usd: float


class FakeClock:
    """A clock the test owns: `sleep` returns at once and moves `now` forward."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._lock = threading.Lock()
        self._parked = threading.Condition(self._lock)
        self._waiting = 0

    def now(self) -> float:
        """Seconds since the Unix epoch, as far as this clock has moved."""
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        """Move `now` forward by `seconds` and return."""
        with self._lock:
            self._now += seconds

    def sleep_until(self, t: float, wake: threading.Event | None = None) -> None:
        """Move `now` forward to `t`, unless it is already later or `wake` is set, and return.

        Threads waiting for one moment wake together, as they would in real time.
        """
        if wake is not None and wake.is_set():
            return
        with self._lock:
            self._now = max(self._now, t)

    def wait(self, condition: threading.Condition, until: float | None = None) -> None:
        """Move `now` forward to `until` and return; with no `until`, wait to be notified.

        `until_waiting` sees each thread waiting with no `until`.
        """
        if until is not None:
            self.sleep_until(until)
            return
        with self._parked:
            self._waiting += 1
            self._parked.notify_all()
        try:
            condition.wait()
        finally:
            with self._parked:
                self._waiting -= 1
                self._parked.notify_all()

    def until_waiting(self, threads: int, timeout: float = 10.0) -> None:
        """Block until `threads` threads wait in `wait` with no `until`.

        Raises
        ------
        TimeoutError
            They did not, within `timeout` real seconds.
        """
        with self._parked:
            if not self._parked.wait_for(lambda: self._waiting >= threads, timeout):
                raise TimeoutError(f"{self._waiting} threads wait, not {threads}")


class FakeGateway:
    """Stands in for the gateway, keeping its rules, with `provider` playing the provider.

    One cache by request form; a hit costs nothing and never counts against a ceiling, and a
    call identical to one in flight waits for it. Each meter's ceiling, with every call in
    flight holding back its largest possible cost, by the real gateway's rule in
    `labllm.ceiling`. A model missing from `prices` is `Rejected`. Every attempt and hit goes
    in `record`. An off-peak-only call that is not a hit gets the peak reply in its price's
    peak hours, and from `peak_margin_s` before they start.

    The provider's tokens are counted as characters: input is the messages' text, or Jev's
    description and its questions as JSON; output is the reply text, or Jev's answers as JSON.

    Examples
    --------
    >>> from labllm import Model
    >>> fake = FakeGateway(lambda form: "hi", prices={"m": Price(0.0, 0.001)})
    >>> with fake.client(repo="docs").meter("example", ceiling_usd=5.0) as m:
    ...     m.chat(Model("openai", "m"), user="hello").text
    'hi'
    """

    def __init__(
        self,
        provider: Provider,
        *,
        prices: Mapping[str, Price],
        clock: FakeClock | None = None,
        peak_margin_s: float = peak.MARGIN_S,
    ) -> None:
        self.clock = clock or FakeClock()
        self._peak_margin_s = peak_margin_s
        self._provider = provider
        self._prices = dict(prices)
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[Any, dict[str, Any], float]] = {}
        self._ceilings = Ceilings()
        self._record: list[Exchange] = []
        self._refusals: float = 0
        self._flights = Flights(self.clock)

    @property
    def record(self) -> list[Exchange]:
        """Every exchange so far, oldest first."""
        with self._lock:
            return list(self._record)

    def client(self, *, repo: str = "tests", person: str | None = None) -> Gateway:
        """Return a `Gateway` client of this fake, on its clock."""
        return Gateway(repo=repo, person=person, transport=self, clock=self.clock)

    def refuse_connections(self, times: int | None = None) -> None:
        """Refuse the next `times` connections, or every one from now on if `times` is None."""
        with self._lock:
            self._refusals = float("inf") if times is None else times

    def send(self, path: str, body: bytes) -> tuple[int, bytes]:
        """Answer one request body, as the gateway answers an HTTP post."""
        with self._lock:
            if self._refusals > 0:
                self._refusals -= 1
                raise ConnectionRefusedError("the fake gateway refused the connection")
        try:
            call = wire.Call.decode(body)
            if path != wire.PATHS[call.form["route"]]:
                raise Rejected(f"{path} does not take a {call.form['route']} call")
            return wire.encode_reply(self._answer(call))
        except (Stopped, Rejected, PeakHours) as e:
            return wire.encode_error(e)

    def _answer(self, call: wire.Call) -> dict[str, Any]:
        key = wire.cache_key(call.form)
        hit = self._hit(call, key)
        if hit is not None:
            return hit
        with self._lock:
            price = self._prices.get(call.form["model"])
        if price is None:
            raise Rejected(f"no price for model {call.form['model']!r}")
        with self._flights.claim(key):
            hit = self._hit(call, key)
            if hit is not None:
                return hit
            return self._attempts(call, key, price)

    def _hit(self, call: wire.Call, key: str) -> dict[str, Any] | None:
        with self._lock:
            hit = self._cache.get(key)
            if hit is None:
                return None
            self._log(call, key, cached=True)
        out, usage, first_cost = hit
        return wire.reply(
            call.form["route"],
            out,
            usage,
            cost_usd=0.0,
            first_cost_usd=first_cost,
            cached=True,
            attempts=0,
        )

    def _attempts(self, call: wire.Call, key: str, price: Price) -> dict[str, Any]:
        form, meter = call.form, call.meter
        input_tokens = _input_tokens(form)
        reserve = largest_cost(price, form)
        retries = Retries(self.clock)
        paid = 0.0
        try:
            while True:
                if call.off_peak_only:
                    peak.check(price.off_peak, self.clock.now(), self._peak_margin_s)
                self._ceilings.hold(call, reserve)
                retries.attempts += 1
                try:
                    out = self._provider(form)
                except Exception as e:
                    failure = unreadable(e, form["provider"])
                    self._ceilings.settle(meter, reserve, 0.0)
                    with self._lock:
                        self._log(call, key, form=form, error=failure.reason)
                    if not isinstance(failure, ProviderUnavailable):
                        raise
                    self.clock.sleep_until(retries.again(failure))
                    continue
                except BaseException:
                    self._ceilings.settle(meter, reserve, 0.0)
                    raise
                chat = form["route"] == "chat"
                output_tokens = len(out) if chat else len(json.dumps(out))
                usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
                cost = (
                    input_tokens * price.input_usd_per_token
                    + output_tokens * price.output_usd_per_token
                )
                empty = None if out else ProviderUnavailable("the provider sent an empty reply")
                self._ceilings.settle(meter, reserve, cost)
                with self._lock:
                    if empty is not None:
                        self._log(call, key, form=form, error=empty.reason, cost_usd=cost)
                    else:
                        self._cache.setdefault(key, (out, usage, cost))
                        reply = {wire.output_field(form["route"]): out}
                        self._log(call, key, form=form, reply=reply, cost_usd=cost)
                if empty is not None:
                    paid += cost
                    self.clock.sleep_until(retries.again(empty))
                    continue
                return wire.reply(
                    form["route"],
                    out,
                    usage,
                    cost_usd=paid + cost,
                    first_cost_usd=cost,
                    cached=False,
                    attempts=retries.attempts,
                )
        except (Stopped, Rejected, PeakHours) as e:
            e.cost_usd, e.attempts = paid, retries.attempts
            raise

    def _log(
        self,
        call: wire.Call,
        key: str,
        *,
        cached: bool = False,
        form: dict[str, Any] | None = None,
        reply: dict[str, Any] | None = None,
        error: str | None = None,
        cost_usd: float = 0.0,
    ) -> None:
        self._record.append(
            Exchange(
                key=key,
                cached=cached,
                meter=call.meter,
                label=call.label,
                ceiling_usd=call.ceiling_usd,
                off_peak_only=call.off_peak_only,
                person=call.person,
                repo=call.repo,
                tag=call.tag,
                form=form,
                reply=reply,
                error=error,
                cost_usd=cost_usd,
            )
        )


def _input_tokens(form: dict[str, Any]) -> int:
    if form["route"] == "chat":
        return sum(len(m["content"]) for m in form["messages"])
    return len(form["description"]) + len(json.dumps(form["questions"]))
