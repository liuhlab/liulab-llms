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

    def sleep_until(self, t: float) -> None:
        """Block the calling thread until `now()` reaches `t`."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> float:
        """Seconds since the Unix epoch."""
        return time.time()

    def sleep(self, seconds: float) -> None:
        """Block the calling thread for `seconds`."""
        time.sleep(seconds)

    def sleep_until(self, t: float) -> None:
        """Block the calling thread until `now()` reaches `t`."""
        while (left := t - time.time()) > 0:
            time.sleep(left)
