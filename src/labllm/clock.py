"""The one clock every wait goes through, so a test can replace it."""

import time
from typing import Protocol


class Clock(Protocol):
    """Wall time and waiting. `labllm.testing.FakeClock` is the test's."""

    def now(self) -> float:
        """Seconds since the Unix epoch."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block the calling thread for `seconds`."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> float:
        """Seconds since the Unix epoch."""
        return time.time()

    def sleep(self, seconds: float) -> None:
        """Block the calling thread for `seconds`."""
        time.sleep(seconds)
