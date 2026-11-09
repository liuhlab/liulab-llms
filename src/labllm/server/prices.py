"""What each call may cost before it is sent, and what it cost after."""

import json
import logging
from collections.abc import Mapping
from types import ModuleType
from typing import Any

from labllm.errors import Rejected
from labllm.model import Price
from labllm.peak import OffPeak

log = logging.getLogger(__name__)


def litellm() -> ModuleType:
    """Import LiteLLM, which prices from the table in the pinned release.

    `labllm.server` sets ``LITELLM_LOCAL_MODEL_COST_MAP`` before this module loads.
    """
    import litellm

    return litellm


class Prices:
    """Prices from the gateway's settings first, then from LiteLLM's pinned table.

    Parameters
    ----------
    settings
        A price for each exact model name the settings price. It wins over the table.
    """

    def __init__(self, settings: Mapping[str, Price] | None = None) -> None:
        self._settings = dict(settings or {})

    def price(self, form: dict[str, Any]) -> Price:
        """Return the price of the form's model at its dearest hour, and its off-peak hours.

        A table model's off-peak hours are its entry's `off_peak_pricing`.

        Raises
        ------
        Rejected
            Neither the settings nor the table price the model.
        """
        name = form["model"]
        if name in self._settings:
            return self._settings[name]
        try:
            info = litellm().get_model_info(model=name, custom_llm_provider=form["provider"])
            return Price(
                float(info["input_cost_per_token"]),
                float(info["output_cost_per_token"]),
                OffPeak.read(info.get("off_peak_pricing")),
            )
        except Exception as e:
            raise Rejected(
                f"no price for model {name!r}: the pinned table lacks it and the gateway's "
                "settings give it none"
            ) from e

    def largest_cost(self, form: dict[str, Any]) -> float:
        """Return the most the call can cost: estimated input plus its full output allowance.

        Input is estimated as `input_bytes`, which no byte-level tokenizer exceeds.
        """
        price = self.price(form)
        return (
            input_bytes(form) * price.input_usd_per_token
            + form["settings"]["max_output_tokens"] * price.output_usd_per_token
        )

    def cost(self, form: dict[str, Any], response: Any) -> float:
        """Return what a LiteLLM response cost.

        LiteLLM's cost function prices a table model, cache hits and off-peak hours included;
        a settings price is applied to the response's token counts.
        """
        name = form["model"]
        usage = response.usage
        if name not in self._settings:
            try:
                return float(
                    litellm().completion_cost(
                        completion_response=response, model=f"{form['provider']}/{name}"
                    )
                )
            except Exception:
                log.exception("LiteLLM could not price a %s response; using its list price", name)
        return self.tokens_cost(form, usage.prompt_tokens, usage.completion_tokens)

    def tokens_cost(self, form: dict[str, Any], input_tokens: int, output_tokens: int) -> float:
        """Return what the form's model charges for these token counts, at its dearest hour."""
        price = self.price(form)
        return input_tokens * price.input_usd_per_token + output_tokens * price.output_usd_per_token


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
