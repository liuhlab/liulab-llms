"""The real Jev provider: one attempt through TypeSafe's own SDK, with the SDK's retries off."""

import json
import threading
from typing import TYPE_CHECKING, Any

from labllm.errors import ProviderExhausted, ProviderUnavailable, Rejected
from labllm.server.app import Attempt, retry_after_s
from labllm.server.prices import Prices

if TYPE_CHECKING:
    import httpx2
    from typesafe_sdk import TypeSafeClient

#: The SDK's errors for a request Jev calls malformed or unauthorized.
_REFUSALS = (
    "TypeSafeBadRequestError",
    "TypeSafeAuthenticationError",
    "TypeSafePermissionDeniedError",
    "TypeSafeNotFoundError",
    "TypeSafeUnprocessableEntityError",
)


class TypeSafeJev:
    """Sends a Jev form through TypeSafe's SDK, once, and returns Jev's answers as it sent them.

    Parameters
    ----------
    prices
        Prices each response from its usage.
    api_key
        Jev's key. By default the SDK reads ``TYPESAFE_API_KEY`` at the first call, so a
        gateway with no key still serves chat.
    transport
        An ``httpx2`` transport under the SDK, so a test can play Jev's HTTP API.
    """

    def __init__(
        self,
        prices: Prices,
        *,
        api_key: str | None = None,
        transport: "httpx2.BaseTransport | None" = None,
    ) -> None:
        self._prices = prices
        self._api_key = api_key
        self._transport = transport
        self._client: TypeSafeClient | None = None
        self._lock = threading.Lock()

    def __call__(self, form: dict[str, Any]) -> Attempt:
        """Send `form` once and return what came back.

        The output is the ``answers`` object of Jev's response body, read from its bytes by the
        standard library, so no answer is dropped, filled in or rounded on the way.

        Raises
        ------
        ProviderExhausted
            Jev answered 402: its prepaid balance is used up.
        Rejected
            Jev refused the request as malformed or unauthorized, the gateway has no key, or the
            form asks for something Jev does not take.
        ProviderUnavailable
            Jev failed in any other way, overload and rate limits included, or its response
            lacks its answers or usage. Empty answers are returned, not raised.
        """
        _check(form)
        import typesafe_sdk as ts

        try:
            response = self._sdk().system_one(
                form["description"], form["questions"], model=form["model"]
            )
        except tuple(getattr(ts, name) for name in _REFUSALS) as e:
            raise Rejected(f"Jev refused the request: {e}") from e
        except ts.TypeSafeAPIError as e:
            if e.status == 402:
                raise ProviderExhausted(f"Jev says its balance is used up: {e}") from e
            wait = retry_after_s(e.headers)
            raise ProviderUnavailable(
                f"Jev failed: {e}", status=e.status, retry_after_s=wait
            ) from e
        except ts.TypeSafeAPIConnectionError as e:
            raise ProviderUnavailable(f"Jev failed: {e}") from e
        except ts.TypeSafeError as e:
            raise Rejected(f"TypeSafe's client refused the request: {e}") from e
        body = json.loads(response.raw_http_response.content)
        try:
            answers, usage = body["answers"], body["usage"]
            cost = self._prices.tokens_cost(form, usage["input_tokens"], usage["output_tokens"])
        except (KeyError, TypeError) as e:
            raise ProviderUnavailable(f"Jev's response lacks its answers or usage: {e!r}") from e
        return Attempt(
            output=answers,
            usage=usage,
            cost_usd=cost,
            response=body,
            input_tokens=usage["input_tokens"],
        )

    def _sdk(self) -> "TypeSafeClient":
        with self._lock:
            if self._client is None:
                import typesafe_sdk as ts

                self._client = ts.TypeSafeClient(
                    api_key=self._api_key,
                    retry=ts.RetryPolicy(max_retries=0),
                    transport=self._transport,
                )
            return self._client


def _check(form: dict[str, Any]) -> None:
    if form["provider"] != "typesafe":
        raise Rejected(f"the jev route takes TypeSafe models, not {form['provider']} ones")
    if not isinstance(form.get("questions"), dict) or not isinstance(form.get("description"), str):
        raise Rejected("a Jev call needs a description and its questions by name")
    unused = [k for k in ("thinking", "effort", "temperature") if form["settings"][k] is not None]
    if unused:
        raise Rejected(f"Jev takes no {', '.join(unused)}")
