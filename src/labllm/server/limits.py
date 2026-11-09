"""Rate limits: a provider's calls wait their turn, first come, first served, never refused."""

import bisect
import math
import threading
from dataclasses import dataclass
from typing import Any

from labllm.clock import Clock
from labllm.server.prices import input_bytes


@dataclass(frozen=True)
class RateLimit:
    """A provider's rate limits, or one model's. None is no limit.

    A per-minute limit is entered as its per-second rate.
    """

    requests_per_second: float | None = None
    input_tokens_per_second: float | None = None
    calls_at_once: int | None = None


def estimate_input_tokens(form: dict[str, Any]) -> int:
    """Estimate a form's input tokens at four bytes of UTF-8 each, as English text runs.

    The provider's usage corrects it after the call. The ceiling keeps its own upper bound.
    """
    return math.ceil(input_bytes(form) / 4)


class _Bucket:
    """Refills at `rate` a second, up to one second's worth; a large take may overdraw it."""

    def __init__(self, rate: float, now: float) -> None:
        self.rate, self.level, self._at = rate, rate, now

    def _fill(self, now: float) -> None:
        if now > self._at:
            self.level = min(self.rate, self.level + (now - self._at) * self.rate)
            self._at = now

    def ready_at(self, need: float, now: float) -> float:
        self._fill(now)
        short = min(need, self.rate) - self.level
        return now if short <= 0 else now + short / self.rate

    def add(self, amount: float, now: float) -> None:
        self._fill(now)
        self.level = min(self.rate, self.level + amount)


class Pause:
    """A provider's pause after overload, shared by every gate of that provider."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._until = 0.0

    @property
    def until(self) -> float:
        """No call to the provider is sent before this time."""
        with self._lock:
            return self._until

    def extend(self, until: float) -> None:
        """Send no call before `until`, nor before any later time already set."""
        with self._lock:
            self._until = max(self._until, until)


class Gate:
    """Where the calls to one provider, or to one model, wait their turn.

    Calls go in order of arrival. The first waits until the provider's pause ends and its
    limits allow it; the others wait behind it.
    """

    def __init__(self, limit: RateLimit, pause: Pause, clock: Clock) -> None:
        now = clock.now()
        self._limit = limit
        self._pause = pause
        self._clock = clock
        self._turns = threading.Condition()
        self._queue: list[int] = []
        self._in_flight = 0
        self._requests = None if limit.requests_per_second is None else _Bucket(
            limit.requests_per_second, now
        )
        self._tokens = None if limit.input_tokens_per_second is None else _Bucket(
            limit.input_tokens_per_second, now
        )

    def wait(self, arrival: int, tokens: int) -> "Turn":
        """Block until it is this call's turn, then take its share of the limits.

        `arrival` orders the calls: a retry keeps its call's first one. `tokens` is the
        call's estimated input.
        """
        with self._turns:
            bisect.insort(self._queue, arrival)
            try:
                while True:
                    now = self._clock.now()
                    ready = self._ready_at(tokens, now) if self._queue[0] == arrival else None
                    if ready is not None and ready <= now:
                        break
                    self._clock.wait(self._turns, ready)
            finally:
                self._queue.remove(arrival)
                self._turns.notify_all()
            self._in_flight += 1
            if self._requests is not None:
                self._requests.add(-1, now)
            if self._tokens is not None:
                self._tokens.add(-tokens, now)
        return Turn(self, tokens)

    def _ready_at(self, tokens: int, now: float) -> float | None:
        """When the first call may go, or None while it waits for a call to finish."""
        if self._limit.calls_at_once is not None and self._in_flight >= self._limit.calls_at_once:
            return None
        ready = max(now, self._pause.until)
        if self._requests is not None:
            ready = max(ready, self._requests.ready_at(1, now))
        if self._tokens is not None:
            ready = max(ready, self._tokens.ready_at(tokens, now))
        return ready

    def _release(self, requests: float, tokens: float) -> None:
        with self._turns:
            now = self._clock.now()
            self._in_flight -= 1
            if self._requests is not None:
                self._requests.add(requests, now)
            if self._tokens is not None:
                self._tokens.add(tokens, now)
            self._turns.notify_all()


class Turn:
    """A call's turn at a gate, held while its attempt is in flight."""

    def __init__(self, gate: Gate, tokens: int) -> None:
        self._gate = gate
        self._tokens = tokens

    def done(self, input_tokens: int | None) -> None:
        """End the attempt, correcting the estimate by the provider's `input_tokens` if known."""
        self._gate._release(0, 0 if input_tokens is None else self._tokens - input_tokens)

    def cancel(self) -> None:
        """Give the turn back whole: its attempt was never sent."""
        self._gate._release(1, self._tokens)
