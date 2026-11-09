"""The errors a call raises: four that stop the caller's loop, and `Rejected`."""


class Stopped(Exception):  # noqa: N818 - the name callers catch
    """A call failed in a way that must stop the caller's loop.

    `reason` names the stop, the same string on every instance of a class. It is also the
    error's code on the wire.
    """

    reason = "stopped"


class CeilingReached(Stopped):
    """The meter's ceiling would be crossed by this call's largest possible cost."""

    reason = "ceiling_reached"


class ProviderExhausted(Stopped):
    """The provider's prepaid balance or monthly cap is used up."""

    reason = "provider_exhausted"


class ProviderUnavailable(Stopped):
    """The provider still failed when the gateway's retry budget ran out."""

    reason = "provider_unavailable"


class GatewayUnreachable(Stopped):
    """The gateway could not be reached, or answered with something no client understands."""

    reason = "gateway_unreachable"


class Rejected(Exception):  # noqa: N818 - the name callers catch
    """The request is malformed or unauthorized, or names a model with no price.

    A bug in the caller or the gateway's settings, so nothing retries it.
    """

    reason = "rejected"
