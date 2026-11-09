"""The errors a call raises: four that stop the caller's loop, and `Rejected`."""

from typing import ClassVar


class Stopped(Exception):  # noqa: N818 - the name callers catch
    """A call failed in a way that must stop the caller's loop: the base of the four stops.

    `reason` names the stop, the same string on every instance of a class. It is also the
    error's code on the wire.
    """

    reason: ClassVar[str]


class CeilingReached(Stopped):
    """The meter's ceiling would be crossed by this call's largest possible cost."""

    reason = "ceiling_reached"


class ProviderExhausted(Stopped):
    """The provider's prepaid balance or monthly cap is used up."""

    reason = "provider_exhausted"


class ProviderUnavailable(Stopped):
    """The provider still failed when the gateway's retry budget ran out.

    Inside the gateway, a provider raises it for each failure worth another attempt, with the
    provider's HTTP `status` (None when no response came) and the `retry_after_s` it asked for.
    """

    reason = "provider_unavailable"

    def __init__(
        self, message: str = "", *, status: int | None = None, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after_s = retry_after_s


class GatewayUnreachable(Stopped):
    """The gateway could not be reached, or answered with something no client understands."""

    reason = "gateway_unreachable"


class Rejected(Exception):  # noqa: N818 - the name callers catch
    """The request is malformed or unauthorized, or names a model with no price.

    A bug in the caller or the gateway's settings, so nothing retries it.
    """

    reason = "rejected"


class PeakHours(Exception):  # noqa: N818 - named for what the gateway says
    """An off-peak-only call reached the gateway in its provider's peak hours.

    The client waits until `until`, in seconds since the Unix epoch, and sends it again, so
    callers never see this.
    """

    reason = "peak_hours"

    def __init__(self, message: str, *, until: float) -> None:
        super().__init__(message)
        self.until = until


#: The four stops: every `Stopped` error a call raises is one of them.
STOPS = (CeilingReached, ProviderExhausted, ProviderUnavailable, GatewayUnreachable)
