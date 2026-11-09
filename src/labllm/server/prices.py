"""What each call may cost before it is sent, and what it cost after."""

import json
import logging
import os
from collections.abc import Mapping
from types import ModuleType
from typing import Any

from labllm.errors import Rejected
from labllm.model import Price

log = logging.getLogger(__name__)


def litellm() -> ModuleType:
    """Import LiteLLM so that it prices from the table in the pinned release.

    Without the setting, LiteLLM downloads the newest table at import, so one pin could price
    one call two ways.
    """
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
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
        """Return the price of the form's model, at its dearest hour.

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
            return Price(float(info["input_cost_per_token"]), float(info["output_cost_per_token"]))
        except Exception as e:
            raise Rejected(
                f"no price for model {name!r}: the pinned table lacks it and the gateway's "
                "settings give it none"
            ) from e

    def largest_cost(self, form: dict[str, Any]) -> float:
        """Return the most the call can cost: estimated input plus its full output allowance.

        Input is estimated as its UTF-8 bytes, which no byte-level tokenizer exceeds.
        """
        price = self.price(form)
        return (
            _input_bytes(form) * price.input_usd_per_token
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
        price = self.price(form)
        return (
            usage.prompt_tokens * price.input_usd_per_token
            + usage.completion_tokens * price.output_usd_per_token
        )


def _input_bytes(form: dict[str, Any]) -> int:
    if form["route"] == "chat":
        return sum(len(m["content"].encode()) for m in form["messages"])
    text = form["description"] + json.dumps(form["questions"], ensure_ascii=False)
    return len(text.encode())
