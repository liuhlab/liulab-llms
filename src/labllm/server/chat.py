"""The real chat provider: one attempt through LiteLLM, with each provider's settings.

A file for DeepSeek is uploaded to DeepSeek's Files API first, which LiteLLM does not reach.
"""

import dataclasses
import json
import os
import threading
import time
from collections.abc import Callable
from types import ModuleType
from typing import Any

from labllm import files
from labllm.errors import ProviderExhausted, ProviderUnavailable, Rejected
from labllm.retry import unreadable
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
    "openai": (
        "insufficient_quota",
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
    ),
    "anthropic": ("credit balance is too low", "reached your specified api usage limits"),
}

_DEEPSEEK = "https://api.deepseek.com"
#: How long DeepSeek keeps an upload. The gateway stops reusing one an hour sooner, so a chat
#: never names a file DeepSeek has just deleted.
_KEEP_S = 24 * 3600
_REUSE_S = _KEEP_S - 3600


class LiteLLMChat:
    """Sends a chat form through `litellm.completion`, once, with no retry of its own.

    The attempt's usage and response are the provider's own body, as LiteLLM's logging hook
    for the call received it, since LiteLLM rewrites some providers' usage; LiteLLM's are
    kept only when that body cannot be read.

    Parameters
    ----------
    prices
        Prices each response.
    completion
        Replaces `litellm.completion`, so a test can play the provider.
    files_api
        An OpenAI client of DeepSeek's Files API, which uploads each file a form carries. By
        default one at DeepSeek's address with the key in ``DEEPSEEK_API_KEY``.
    """

    def __init__(
        self,
        prices: Prices,
        *,
        completion: Callable[..., Any] | None = None,
        files_api: Any = None,
    ) -> None:
        self._prices = prices
        self._completion = completion
        self._uploads = _Uploads(files_api)

    def as_sent(self, form: dict[str, Any]) -> dict[str, Any]:
        """Return `form` with the messages its provider is sent, for its reservation to count."""
        return {**form, "messages": _messages(form)}

    def __call__(self, form: dict[str, Any]) -> Attempt:
        """Send `form` once, after uploading each file it carries, and return what came back.

        A file's parsing is paid by the attempt that uploads it: in its cost, or in the
        `cost_usd` of the error it raises if the chat then fails.

        Raises
        ------
        ProviderExhausted
            The provider says its balance or cap is used up.
        Rejected
            The provider refused the request or a file as malformed or unauthorized, or the
            form asks for a setting its provider does not take.
        ProviderUnavailable
            The provider failed in any other way. An empty reply is returned, not raised.
        """
        parsing_usd = 0.0
        try:
            file_ids = []
            for f in files.attached(form):
                file_id, usd = self._uploads.file_id(f)
                file_ids.append(file_id)
                parsing_usd += usd
            attempt = self._send(_by_file_id(form, file_ids))
        except Exception as e:
            if not parsing_usd:
                raise
            failure = unreadable(e, form["provider"])
            failure.cost_usd = parsing_usd
            if failure is e:
                raise
            raise failure from e
        return dataclasses.replace(attempt, cost_usd=attempt.cost_usd + parsing_usd)

    def _send(self, form: dict[str, Any]) -> Attempt:
        ll = litellm()
        import openai

        completion = self._completion or ll.completion
        received: list[Any] = []

        def keep(details: dict[str, Any]) -> None:
            if details.get("log_event_type") == "post_api_call":
                received.append(details.get("original_response"))

        try:
            response = completion(**request(form), logger_fn=keep)
        except openai.APIError as e:
            raise _failure(ll, form["provider"], e) from e
        body = _body(received[-1] if received else None)
        usage = body.get("usage") if body is not None else None
        if not isinstance(body, dict) or not isinstance(usage, dict):
            body = response.model_dump(mode="json")
            usage = response.usage.model_dump(mode="json", exclude_none=True)
        message = response.choices[0].message
        output, tool_calls = message.content or "", None
        if form["provider"] == "deepseek":
            output, tool_calls = _functions_called(form, message)
        return Attempt(
            output=output,
            usage=usage,
            cost_usd=self._prices.cost(form, response),
            response=body,
            input_tokens=response.usage.prompt_tokens,
            tool_calls=tool_calls,
        )


def _functions_called(form: dict[str, Any], message: Any) -> tuple[str, list[Any] | None]:
    """Return a DeepSeek reply's output and the caller's tools it asks for.

    With a schema, a call to `answer` is the final answer: its arguments are the output. The
    tools are None for a form without tools, and empty for a final answer.
    """
    calls = message.tool_calls or []
    no_calls = [] if form.get("tools") else None
    if form.get("schema") is not None:
        for call in calls:
            if call.function.name == "answer":
                return call.function.arguments or "", no_calls
    if form.get("tools") and calls:
        asked = [
            {"id": c.id, "name": c.function.name, "arguments": c.function.arguments} for c in calls
        ]
        return message.content or "", asked
    return ("" if form.get("schema") is not None else message.content or ""), no_calls


def _body(original: Any) -> dict[str, Any] | None:
    """Return the provider's response body as LiteLLM's logging hook passed it, if it is JSON."""
    if isinstance(original, str):
        try:
            original = json.loads(original)
        except ValueError:
            return None
    return original if isinstance(original, dict) else None


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
    request body. A DeepSeek schema becomes one strict function call, which requires thinking
    off. An agent's tools go to DeepSeek as strict functions too, beside the schema's `answer`;
    with a schema, the model must call one of them. Other providers get native schema output
    and the effort as `reasoning_effort`, and refuse `thinking`. OpenAI's own client retries
    unless told not to.

    Raises
    ------
    Rejected
        The form names the Jev provider, `thinking` for a provider other than DeepSeek, tools
        for one, or a tool named `answer` beside a schema.
    """
    provider, settings, schema = form["provider"], form["settings"], form.get("schema")
    tools = form.get("tools") or []
    if provider == "typesafe":
        raise Rejected("Jev calls take the jev route")
    if tools and provider != "deepseek":
        raise Rejected(f"the gateway sends an agent's tools to DeepSeek only, not {provider}")
    if schema is not None and any(tool["name"] == "answer" for tool in tools):
        raise Rejected("an agent's tool may not be named answer: the schema's function is")
    kwargs: dict[str, Any] = {
        "model": f"{provider}/{form['model']}",
        "messages": _messages(form),
        "max_tokens": settings["max_output_tokens"],
    }
    if settings["temperature"] is not None:
        kwargs["temperature"] = settings["temperature"]
    if provider == "deepseek":
        functions = [{**tool, "strict": True} for tool in tools]
        if schema is not None:
            functions.append({"name": "answer", "parameters": schema, "strict": True})
        if functions:
            if settings["thinking"] is True:
                raise Rejected("DeepSeek strict function calls need thinking off")
            kwargs["thinking"] = {"type": "disabled"}
            kwargs["_skip_mcp_handler"] = True
            kwargs["tools"] = [{"type": "function", "function": f} for f in functions]
            if schema is not None:
                kwargs["tool_choice"] = (
                    "required" if tools else {"type": "function", "function": {"name": "answer"}}
                )
        elif settings["thinking"] is not None:
            kwargs["thinking"] = {"type": "enabled" if settings["thinking"] else "disabled"}
        if settings["effort"] is not None:
            kwargs["extra_body"] = {"reasoning_effort": settings["effort"]}
    if provider != "deepseek" and settings["thinking"] is not None:
        raise Rejected(f"thinking is a DeepSeek setting; for {provider}, set effort instead")
    if provider != "deepseek" and settings["effort"] is not None:
        kwargs["reasoning_effort"] = settings["effort"]
    if provider != "deepseek" and schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema, "strict": True},
        }
    if provider == "openai":
        kwargs["max_retries"] = 0
    if provider == "deepseek" and any(files.is_file(p) for p in _parts(kwargs["messages"])):
        # LiteLLM cuts a DeepSeek message with any part but text and images down to its text.
        kwargs["extra_body"] = {**kwargs.get("extra_body", {}), "messages": kwargs["messages"]}
    return kwargs


def _parts(messages: list[dict[str, Any]]) -> list[Any]:
    return [p for m in messages if isinstance(m.get("content"), list) for p in m["content"]]


def _by_file_id(form: dict[str, Any], file_ids: list[str]) -> dict[str, Any]:
    """Return `form` with its file blocks, in order, replaced by DeepSeek's references to them."""
    if not file_ids:
        return form
    ids = iter(file_ids)

    def swap(part: Any) -> Any:
        return {"type": "file", "file_id": next(ids)} if files.is_file(part) else part

    messages = [
        {**m, "content": [swap(p) for p in m["content"]]}
        if isinstance(m.get("content"), list)
        else m
        for m in form["messages"]
    ]
    return {**form, "messages": messages}


class _Uploads:
    """The files the gateway has uploaded to DeepSeek, so each is parsed and paid for once a day.

    Each file uploads once at a time. The same bytes under another kind upload apart, since
    each kind has its own purpose.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._busy: dict[tuple[str, str], threading.Lock] = {}
        self._ids: dict[tuple[str, str], tuple[str, float]] = {}

    def file_id(self, f: files.Attached) -> tuple[str, float]:
        """Return DeepSeek's id for `f` and what this call paid to parse it.

        A recent upload of the same file is reused, at no cost. A new document's parsing
        counts as `files.Attached.most_usd`, since DeepSeek reports no page count.

        Raises
        ------
        Rejected
            DeepSeek refused the file, or the gateway has no DeepSeek key.
        ProviderExhausted, ProviderUnavailable
            As for a chat request.
        """
        key = (f.kind, f.sha256)
        with self._lock:
            busy = self._busy.setdefault(key, threading.Lock())
        with busy:
            with self._lock:
                known = self._ids.get(key)
            if known is not None and time.monotonic() - known[1] < _REUSE_S:
                return known[0], 0.0
            file_id = self._upload(f)
            with self._lock:
                self._ids[key] = (file_id, time.monotonic())
            return file_id, f.most_usd

    def _upload(self, f: files.Attached) -> str:
        import openai

        if self._client is None:
            key = os.environ.get("DEEPSEEK_API_KEY")
            if not key:
                raise Rejected("the gateway has no DeepSeek key to upload a file with")
            self._client = openai.OpenAI(api_key=key, base_url=_DEEPSEEK, max_retries=0)
        # OpenAI's client types purposes by OpenAI's list, which lacks DeepSeek's parsing one.
        purpose: Any = "text_recognition" if f.kind == "document" else "user_data"
        try:
            uploaded = self._client.files.create(
                file=(f.filename, f.data),
                purpose=purpose,
                expires_after={"anchor": "created_at", "seconds": _KEEP_S},
            )
        except openai.APIStatusError as e:
            if e.status_code == 402:
                raise ProviderExhausted(f"deepseek says its balance is used up: {e}") from e
            if e.status_code in (400, 401, 403, 404, 413, 415, 422):
                raise Rejected(f"deepseek refused the file {f.filename!r}: {e}") from e
            raise ProviderUnavailable(
                f"deepseek failed to take the file {f.filename!r}: {e}",
                status=e.status_code,
                retry_after_s=retry_after_s(e.response.headers),
            ) from e
        except openai.APIError as e:
            raise ProviderUnavailable(f"deepseek failed to take {f.filename!r}: {e}") from e
        return uploaded.id


def _messages(form: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the messages a chat form's provider is sent, leaving the form as it is."""
    return form["messages"]
