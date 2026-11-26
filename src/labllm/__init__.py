"""The Liu Lab's client for its model gateway, and the engine that runs model work over units."""

from importlib.metadata import version

from labllm.client import AgentReply, ChatReply, Gateway, JevReply, Meter, Tool
from labllm.engine import (
    Held,
    Inputs,
    PassResult,
    QueueEntry,
    Role,
    Task,
    TaskReader,
    Unit,
    UnitEnd,
    UnitKind,
)
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
from labllm.state import (
    ENDINGS,
    NOT_ENDINGS,
    PASS_ENDINGS,
    CheckResult,
    NoTask,
    Outcome,
    Pass,
    Stamp,
    TaskBusy,
    TaskInUse,
    TaskNeedsRepair,
    UnitCost,
    UnknownLayout,
)

#: Read from the installed distribution's metadata, which hatch-vcs fills from the newest
#: git tag. No version string is written by hand anywhere in this repo.
__version__ = version("liulab-llms")

__all__ = [
    "ENDINGS",
    "NOT_ENDINGS",
    "PASS_ENDINGS",
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
    "NoTask",
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
    "TaskBusy",
    "TaskInUse",
    "TaskNeedsRepair",
    "TaskReader",
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
