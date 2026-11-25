"""What a call may cost, and each meter's ceiling: one rule for the fake and the real gateway."""

import json
import threading
from collections.abc import Callable
from typing import Any

from labllm import files, wire
from labllm.errors import CeilingReached, Rejected
from labllm.model import Price

#: The most input tokens each provider bills for one image, whatever its size or detail level,
#: from its own documentation; `docs/adr/0015-an-image-counts-as-its-providers-bound.md` cites it.
IMAGE_TOKENS = {"anthropic": 4784, "deepseek": 1024, "openai": 73800}

#: The content parts the ceiling can bound. A chat form with any other part is refused.
PART_TYPES = ("text", "image_url", "file")


def input_bytes(form: dict[str, Any]) -> int:
    """Return the UTF-8 bytes of a form's input as JSON, leaving out its images and files.

    For chat, the messages with their roles, each image or file part cut to its type, the
    schema and any tools; for Jev, its description and questions.
    """
    if form["route"] == "chat":
        sent = [[_without_media(m) for m in form["messages"]], form.get("schema")]
        if form.get("tools"):
            sent.append(form["tools"])
    else:
        sent = [form["description"], form["questions"]]
    return len(json.dumps(sent, ensure_ascii=False, separators=(",", ":")).encode())


def image_tokens(form: dict[str, Any]) -> int:
    """Return the most input tokens a form's images can be billed: its provider's bound each.

    Raises
    ------
    Rejected
        The form has an image, and `IMAGE_TOKENS` has no bound for its provider.
    """
    images = sum(_is_image(part) for part in _parts(form))
    if not images:
        return 0
    bound = IMAGE_TOKENS.get(form["provider"])
    if bound is None:
        raise Rejected(f"the gateway has no bound on what {form['provider']} bills for an image")
    return images * bound


def largest_cost(price: Price, form: dict[str, Any]) -> float:
    """Return the most a call can cost: its input plus its full output allowance.

    Input is counted as `input_bytes`, which no byte-level tokenizer exceeds, plus
    `image_tokens`. A form with a file counts its input as its provider's context length, since
    a file's text has no bound short of it, plus the most each file's parsing can cost.

    Raises
    ------
    Rejected
        The form has a content part whose type is not in `PART_TYPES`, or as `image_tokens`
        or `labllm.files.attached` does.
    """
    _check_parts(form)
    attached = files.attached(form)
    input_tokens = input_bytes(form) + image_tokens(form)
    if attached:
        input_tokens = files.CONTEXT_TOKENS[form["provider"]]
    return (
        input_tokens * price.input_usd_per_token
        + form["settings"]["max_output_tokens"] * price.output_usd_per_token
        + sum(f.most_usd for f in attached)
    )


def _parts(form: dict[str, Any]) -> list[Any]:
    """Return the content parts of a chat form's messages; text content has none."""
    if form["route"] != "chat":
        return []
    return [part for m in form["messages"] for part in _content_parts(m)]


def _check_parts(form: dict[str, Any]) -> None:
    for part in _parts(form):
        kind = part.get("type") if isinstance(part, dict) else type(part).__name__
        if kind not in PART_TYPES:
            raise Rejected(
                f"the gateway cannot bound what a {kind!r} content part costs: "
                f"give only {', '.join(PART_TYPES)}"
            )


def _content_parts(message: Any) -> list[Any]:
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def _is_image(part: Any) -> bool:
    return isinstance(part, dict) and part.get("type") == "image_url"


def _without_media(message: Any) -> Any:
    parts = _content_parts(message)
    if not parts:
        return message
    return {**message, "content": [_cut(p) for p in parts]}


def _cut(part: Any) -> Any:
    if _is_image(part):
        return {"type": "image_url"}
    return {"type": "file"} if files.is_file(part) else part


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
