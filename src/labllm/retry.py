"""When a gateway tries an unavailable provider again: one rule for the fake and the real one."""

from labllm.clock import Clock
from labllm.errors import ProviderUnavailable

#: How long a call keeps trying after its first failure, unless the gateway's settings say.
BUDGET_S = 300.0
_FIRST_WAIT_S = 1.0
_LONGEST_WAIT_S = 60.0


class Retries:
    """One call's attempts at a provider: when to try again, and when to give up.

    The caller adds one to `attempts` as it sends each.
    """

    def __init__(self, clock: Clock, budget_s: float = BUDGET_S) -> None:
        self.attempts = 0
        self._clock = clock
        self._budget_s = budget_s
        self._deadline: float | None = None

    def again(self, error: ProviderUnavailable) -> float:
        """Return the time to try again after `error`, the latest attempt's failure.

        The provider's `retry_after_s` wins; otherwise the wait doubles from one second, up to
        a minute. The budget runs from the first failure.

        Raises
        ------
        ProviderUnavailable
            The next attempt would start after the budget ends.
        """
        now = self._clock.now()
        if self._deadline is None:
            self._deadline = now + self._budget_s
        wait = error.retry_after_s
        if wait is None:
            wait = min(_FIRST_WAIT_S * 2 ** max(self.attempts - 1, 0), _LONGEST_WAIT_S)
        if now + wait > self._deadline:
            raise ProviderUnavailable(
                f"still failing after {self.attempts} attempts: {error}", status=error.status
            ) from error
        return now + wait
