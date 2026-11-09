"""The library's side of the wire: the request form, its cache key, and what travels over HTTP.

The client, the fake gateway and the real gateway all build these from this module, so they
agree on every key. A call is a JSON `Call` posted to the path for its form's route. A reply
comes back as status 200 and a JSON object; an error as another status and
``{"error": <reason>, "message": <text>, "cost_usd": <paid>, "attempts": <sent>}``, where the
reason names an error class.
"""

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, TypeGuard

from labllm.errors import STOPS, GatewayUnreachable, PeakHours, Rejected, Stopped
from labllm.model import Model

#: The path each route is posted to.
PATHS = {"chat": "/chat", "jev": "/jev"}

_ERRORS: dict[str, type[Stopped | Rejected]] = {cls.reason: cls for cls in (*STOPS, Rejected)}

#: The HTTP status a gateway answers each error with. A client reads the reason, not this.
STATUS = {
    "ceiling_reached": 402,
    "provider_exhausted": 402,
    "provider_unavailable": 503,
    "gateway_unreachable": 502,
    "rejected": 400,
    "peak_hours": 503,
}


def chat_form(
    model: Model, *, user: str, system: str | None, schema: dict[str, Any] | None
) -> dict[str, Any]:
    """Build the request form of a chat call: what decides the answer, and nothing else."""
    messages = [] if system is None else [{"role": "system", "content": system}]
    messages.append({"role": "user", "content": user})
    return {**_model_part("chat", model), "messages": messages, "schema": schema}


def jev_form(model: Model, *, description: str, questions: Mapping[str, Any]) -> dict[str, Any]:
    """Build the request form of a Jev call: its questions keep their names and order."""
    return {
        **_model_part("jev", model),
        "description": description,
        "questions": dict(questions),
    }


def _model_part(route: str, model: Model) -> dict[str, Any]:
    temperature = None if model.temperature is None else float(model.temperature)
    return {
        "route": route,
        "provider": model.provider,
        "model": model.name,
        "settings": {
            "thinking": model.thinking,
            "effort": model.effort,
            "temperature": temperature,
            "max_output_tokens": model.max_output_tokens,
        },
    }


def cache_key(form: dict[str, Any]) -> str:
    """Return the cache key of a request form, as a gateway decoded it.

    Key order is kept, not sorted: a schema's property order can change what a model writes.

    Examples
    --------
    >>> form = chat_form(Model("openai", "gpt-5"), user="hi", system=None, schema=None)
    >>> len(cache_key(form))
    64
    """
    text = json.dumps(form, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Call:
    """One call as the library sends it: the request form and the labels around it.

    Only `form` reaches the cache key. `meter` is the meter's identity; `label` is for people.
    """

    form: dict[str, Any]
    meter: str
    label: str
    ceiling_usd: float
    off_peak_only: bool
    person: str
    repo: str
    tag: str | None

    def encode(self) -> bytes:
        """Encode the request body."""
        return json.dumps(asdict(self), ensure_ascii=False, allow_nan=False).encode()

    @classmethod
    def decode(cls, body: bytes) -> "Call":
        """Read a request body.

        Raises
        ------
        Rejected
            The body is not a call.
        """
        try:
            call = cls(**json.loads(body))
        except (ValueError, TypeError) as e:
            raise Rejected(f"not a call: {e}") from e
        if not isinstance(call.form, dict) or call.form.get("route") not in PATHS:
            raise Rejected("not a call: its form names no known route")
        return call


def encode_reply(reply: dict[str, Any]) -> tuple[int, bytes]:
    """Encode a reply as a status and body."""
    return 200, json.dumps(reply, ensure_ascii=False).encode()


def encode_error(error: Stopped | Rejected | PeakHours) -> tuple[int, bytes]:
    """Encode an error as a status and body, with what the call paid. A peak reply adds `until`."""
    body: dict[str, Any] = {
        "error": error.reason,
        "message": str(error),
        "cost_usd": error.cost_usd,
        "attempts": error.attempts,
    }
    if isinstance(error, PeakHours):
        body["until"] = error.until
    return STATUS[error.reason], json.dumps(body, ensure_ascii=False).encode()


def decode_response(status: int, body: bytes) -> dict[str, Any]:
    """Return the reply in a response, or raise the error it carries.

    Raises
    ------
    Stopped, Rejected, PeakHours
        The class the response's reason names, with the cost and attempts it carries, or none.
        A response no client understands raises `GatewayUnreachable`.
    """
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if isinstance(data, dict):
        if status == 200 and "error" not in data:
            return data
        reason, message = data.get("error"), str(data.get("message", ""))
        cls = _ERRORS.get(reason) if isinstance(reason, str) else None
        until = data.get("until")
        error: Stopped | Rejected | PeakHours | None = None
        if cls is not None:
            error = cls(message)
        elif reason == PeakHours.reason and _seconds(until):
            error = PeakHours(message, until=float(until))
        if error is not None:
            cost, attempts = data.get("cost_usd"), data.get("attempts")
            error.cost_usd = float(cost) if _seconds(cost) else 0.0
            error.attempts = attempts if isinstance(attempts, int) and attempts > 0 else 0
            raise error
    raise GatewayUnreachable(f"the gateway answered {status}: {body[:200]!r}")


def _seconds(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
