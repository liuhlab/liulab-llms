"""What a call may cost, and each meter's ceiling: one rule for the fake and the real gateway."""

import json
import threading
from collections.abc import Callable
from typing import Any

from labllm import wire
from labllm.errors import CeilingReached
from labllm.model import Price


def input_bytes(form: dict[str, Any]) -> int:
    """Return the UTF-8 bytes of everything a form sends as input, as JSON.

    For chat, the messages with their roles, and the schema; for Jev, its description and
    questions.
    """
    if form["route"] == "chat":
        sent = [form["messages"], form.get("schema")]
    else:
        sent = [form["description"], form["questions"]]
    return len(json.dumps(sent, ensure_ascii=False, separators=(",", ":")).encode())


def largest_cost(price: Price, form: dict[str, Any]) -> float:
    """Return the most a call can cost: its input plus its full output allowance.

    Input is counted as `input_bytes`, which no byte-level tokenizer exceeds.
    """
    return (
        input_bytes(form) * price.input_usd_per_token
        + form["settings"]["max_output_tokens"] * price.output_usd_per_token
    )


class Ceilings:
    """Each meter's spend, and what its calls in flight hold back from its ceiling.

    Parameters
    ----------
    spent_before
        What a meter paid before this gateway first saw it, as the record says after a restart.
    """

    def __init__(self, spent_before: Callable[[str], float] | None = None) -> None:
        self._spent_before = spent_before or (lambda meter: 0.0)
        self._lock = threading.Lock()
        self._spent: dict[str, float] = {}
        self._held: dict[str, float] = {}

    def hold(self, call: wire.Call, reserve: float) -> None:
        """Hold back `reserve` from the call's meter until `settle`.

        Raises
        ------
        CeilingReached
            What the meter spent and holds, plus `reserve`, would cross its ceiling.
        """
        meter = call.meter
        with self._lock:
            if meter not in self._spent:
                self._spent[meter] = self._spent_before(meter)
            held = self._spent[meter] + self._held.get(meter, 0.0)
            if held + reserve > call.ceiling_usd:
                raise CeilingReached(
                    f"meter {call.label!r} has {held:.6f} USD spent or held, and this call may "
                    f"cost {reserve:.6f} more, over its ceiling of {call.ceiling_usd:.6f}"
                )
            self._held[meter] = self._held.get(meter, 0.0) + reserve

    def settle(self, meter: str, reserve: float, cost_usd: float) -> None:
        """Release a hold of `reserve`, and count what its attempt cost."""
        with self._lock:
            self._held[meter] -= reserve
            self._spent[meter] += cost_usd
