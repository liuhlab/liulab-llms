"""The Liu Lab's client for its model gateway, and the engine that runs model work over units."""

from importlib.metadata import version

from labllm.client import AgentReply, ChatReply, Gateway, JevReply, Meter, Tool
from labllm.engine import Held, Inputs, PassResult, QueueEntry, Role, Task, Unit, UnitEnd, UnitKind
from labllm.errors import (
    CeilingReached,
    GatewayUnreachable,
    ProviderExhausted,
    ProviderUnavailable,
    Rejected,
    StepLimitReached,
    Stopped,
    UnknownTool,
)
from labllm.model import Model
from labllm.replies import ReplyNotJson, reply_json
from labllm.reviews import ChecksFailed, Review, Reviews
from labllm.state import CheckResult, Outcome, Pass, Stamp, TaskInUse, UnitCost, UnknownLayout

#: Read from the installed distribution's metadata, which hatch-vcs fills from the newest
#: git tag. No version string is written by hand anywhere in this repo.
__version__ = version("liulab-llms")

__all__ = [
    "AgentReply",
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
    "Pass",
    "PassResult",
    "ProviderExhausted",
    "ProviderUnavailable",
    "QueueEntry",
    "Rejected",
    "ReplyNotJson",
    "Review",
    "Reviews",
    "Role",
    "Stamp",
    "StepLimitReached",
    "Stopped",
    "Task",
    "TaskInUse",
    "Tool",
    "Unit",
    "UnitCost",
    "UnitEnd",
    "UnitKind",
    "UnknownLayout",
    "UnknownTool",
    "__version__",
    "reply_json",
]
