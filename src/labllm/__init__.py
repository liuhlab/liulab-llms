"""The Liu Lab's client for its model gateway: blocking chat and Jev calls under a spend ceiling."""

from importlib.metadata import version

from labllm.client import ChatReply, Gateway, JevReply, Meter
from labllm.errors import (
    CeilingReached,
    GatewayUnreachable,
    ProviderExhausted,
    ProviderUnavailable,
    Rejected,
    Stopped,
)
from labllm.model import Model

#: Read from the installed distribution's metadata, which hatch-vcs fills from the newest
#: git tag. No version string is written by hand anywhere in this repo.
__version__ = version("liulab-llms")

__all__ = [
    "CeilingReached",
    "ChatReply",
    "Gateway",
    "GatewayUnreachable",
    "JevReply",
    "Meter",
    "Model",
    "ProviderExhausted",
    "ProviderUnavailable",
    "Rejected",
    "Stopped",
    "__version__",
]
