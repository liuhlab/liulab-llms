"""The Liu Lab's client for its model gateway, and the engine that runs model work over units."""

from importlib.metadata import version

from labllm.client import ChatReply, Gateway, JevReply, Meter
from labllm.engine import Held, Inputs, PassResult, QueueEntry, Role, Task, Unit, UnitKind
from labllm.errors import (
    CeilingReached,
    GatewayUnreachable,
    ProviderExhausted,
    ProviderUnavailable,
    Rejected,
    Stopped,
)
from labllm.model import Model
from labllm.reviews import ChecksFailed, Review, Reviews
from labllm.state import CheckResult, Outcome, Stamp, TaskInUse, UnitCost, UnknownLayout

#: Read from the installed distribution's metadata, which hatch-vcs fills from the newest
#: git tag. No version string is written by hand anywhere in this repo.
__version__ = version("liulab-llms")

__all__ = [
    "CeilingReached",
    "ChatReply",
    "CheckResult",
    "ChecksFailed",
    "Gateway",
    "GatewayUnreachable",
    "Held",
    "Inputs",
    "JevReply",
    "Meter",
    "Model",
    "Outcome",
    "PassResult",
    "ProviderExhausted",
    "ProviderUnavailable",
    "QueueEntry",
    "Rejected",
    "Review",
    "Reviews",
    "Role",
    "Stamp",
    "Stopped",
    "Task",
    "TaskInUse",
    "Unit",
    "UnitCost",
    "UnitKind",
    "UnknownLayout",
    "__version__",
]
