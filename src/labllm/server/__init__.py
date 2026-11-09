"""The lab gateway: answers the library's calls, keeps the record, and holds each ceiling.

Start it with ``python -m labllm.server``. It needs the ``gateway`` extra.
"""

import os

# The one place it is set, before anything here can import LiteLLM. Without it, LiteLLM
# downloads the newest price table at import, so one pin could price one call two ways.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from labllm.server.app import Attempt, Provider, Server
from labllm.server.prices import Prices
from labllm.server.record import Record

__all__ = ["Attempt", "Prices", "Provider", "Record", "Server"]
