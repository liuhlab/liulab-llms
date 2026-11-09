"""One attempt at a time for each cache key: one rule for the fake and the real gateway."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from labllm.clock import Clock


class Flights:
    """The cache keys a call is trying a provider for.

    A call that misses the cache claims its key, then reads the cache again. An identical call
    waits for the claim to end, so it is served from the cache at no cost, or tries itself if
    the first call failed. So a request in flight is never paid twice.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._changed = threading.Condition()
        self._keys: set[str] = set()

    @contextmanager
    def claim(self, key: str) -> Iterator[None]:
        """Hold `key` for the block, once no other call holds it."""
        with self._changed:
            while key in self._keys:
                self._clock.wait(self._changed)
            self._keys.add(key)
        try:
            yield
        finally:
            with self._changed:
                self._keys.discard(key)
                self._changed.notify_all()
