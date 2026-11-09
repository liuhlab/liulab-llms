"""The real chat provider: one attempt through LiteLLM, with each provider's settings."""

from collections.abc import Callable
from types import ModuleType
from typing import Any

from labllm.errors import ProviderExhausted, ProviderUnavailable, Rejected
from labllm.server.app import Attempt, retry_after_s
from labllm.server.prices import Prices, litellm

#: LiteLLM's errors for a request the provider calls malformed or unauthorized.
_REFUSALS = (
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "UnprocessableEntityError",
)

#: How each provider's error body says a balance or cap is used up, besides a 402 status.
#: OpenAI answers 429 for a used-up quota; Anthropic has answered 400 for a used-up credit.
_USED_UP = {
    "openai": ("insufficient_quota",),
    "anthropic": ("credit balance is too low", "reached your specified api usage limits"),
}


class LiteLLMChat:
    """Sends a chat form through `litellm.completion`, once, with no retry of its own.

    Parameters
    ----------
    prices
        Prices each response.
    completion
        Replaces `litellm.completion`, so a test can play the provider.
    """

    def __init__(self, prices: Prices, *, completion: Callable[..., Any] | None = None) -> None:
        self._prices = prices
        self._completion = completion

    def __call__(self, form: dict[str, Any]) -> Attempt:
        """Send `form` once and return what came back.

        Raises
        ------
        ProviderExhausted
            The provider says its balance or cap is used up.
        Rejected
            The provider refused the request as malformed or unauthorized, or the form asks
            for a setting its provider does not take.
        ProviderUnavailable
            The provider failed in any other way. An empty reply is returned, not raised.
        """
        ll = litellm()
        import openai

        completion = self._completion or ll.completion
        try:
            response = completion(**request(form))
        except openai.APIError as e:
            raise _failure(ll, form["provider"], e) from e
        return Attempt(
            output=response.choices[0].message.content or "",
            usage=response.usage.model_dump(mode="json", exclude_none=True),
            cost_usd=self._prices.cost(form, response),
            response=response.model_dump(mode="json"),
            input_tokens=response.usage.prompt_tokens,
        )


def _failure(ll: ModuleType, provider: str, e: Exception) -> Exception:
    status = getattr(e, "status_code", None)
    said = f"{e} {getattr(e, 'body', '')}".lower()
    if status == 402 or any(words in said for words in _USED_UP.get(provider, ())):
        return ProviderExhausted(f"{provider} says its balance or cap is used up: {e}")
    if isinstance(e, tuple(getattr(ll, name) for name in _REFUSALS)):
        return Rejected(f"{provider} refused the request: {e}")
    headers = getattr(e, "litellm_response_headers", None)
    return ProviderUnavailable(
        f"{provider} failed: {e}", status=status, retry_after_s=retry_after_s(headers)
    )


def request(form: dict[str, Any]) -> dict[str, Any]:
    """Return the arguments to `litellm.completion` for a chat form.

    DeepSeek takes `thinking` on or off and its effort level, which LiteLLM would drop, in the
    request body; it takes JSON mode, not a schema. Other providers get the schema as native
    schema output and the effort as `reasoning_effort`, and refuse `thinking`. OpenAI's own
    client retries unless told not to.

    Raises
    ------
    Rejected
        The form names the Jev provider, or `thinking` for a provider other than DeepSeek.
    """
    provider, settings, schema = form["provider"], form["settings"], form.get("schema")
    if provider == "typesafe":
        raise Rejected("Jev calls take the jev route")
    kwargs: dict[str, Any] = {
        "model": f"{provider}/{form['model']}",
        "messages": form["messages"],
        "max_tokens": settings["max_output_tokens"],
    }
    if settings["temperature"] is not None:
        kwargs["temperature"] = settings["temperature"]
    if provider == "deepseek":
        if settings["thinking"] is not None:
            kwargs["thinking"] = {"type": "enabled" if settings["thinking"] else "disabled"}
        if settings["effort"] is not None:
            kwargs["extra_body"] = {"reasoning_effort": settings["effort"]}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs
    if settings["thinking"] is not None:
        raise Rejected(f"thinking is a DeepSeek setting; for {provider}, set effort instead")
    if settings["effort"] is not None:
        kwargs["reasoning_effort"] = settings["effort"]
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema, "strict": True},
        }
    if provider == "openai":
        kwargs["max_retries"] = 0
    return kwargs
