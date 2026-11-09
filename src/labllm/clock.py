"""The one clock every wait goes through, so a test can replace it."""

import threading
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

    def sleep_until(self, t: float, wake: threading.Event | None = None) -> None:
        """Block the calling thread until `now()` reaches `t`, or until `wake` is set."""
        ...

    def wait(self, condition: threading.Condition, until: float | None = None) -> None:
        """Wait on `condition`, whose lock the caller holds, until notified or `until`."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> float:
        """Seconds since the Unix epoch."""
        return time.time()

    def sleep(self, seconds: float) -> None:
        """Block the calling thread for `seconds`."""
        time.sleep(seconds)

    def sleep_until(self, t: float, wake: threading.Event | None = None) -> None:
        """Block the calling thread until `now()` reaches `t`, or until `wake` is set."""
        while (left := t - time.time()) > 0:
            if wake is None:
                time.sleep(left)
            elif wake.wait(left):
                return

    def wait(self, condition: threading.Condition, until: float | None = None) -> None:
        """Wait on `condition`, whose lock the caller holds, until notified or `until`."""
        condition.wait(None if until is None else max(0.0, until - time.time()))
