---
search:
  exclude: true
---

# Jev through TypeSafe's SDK

`labllm.server.jev.TypeSafeJev` sends each `/jev` call once through TypeSafe's own SDK.
Four choices cost something if reversed.

**Questions are a mapping of names to Jev's own question objects**, not a list. Jev's API
takes named questions and answers under the same names, so the form carries them as the
caller wrote them, in order, and `JevReply.answers` is Jev's `answers` object. The spec's
list would have meant names made up from positions. The cost: changing the shape now
changes every Jev cache key, and callers write Jev's question JSON themselves.

**Answers are read from the response's bytes by the standard library.** The SDK's typed
answers drop an answer type the SDK does not know and turn score levels into integers. The
same decoder reads the record and the wire, so a probability arrives as the float Jev sent.
The SDK still checks the body against Jev's schema first: a body that fails it comes back
unavailable rather than half-read.

**The SDK's retries are off** (`RetryPolicy(max_retries=0)`). A refusal (400, 401, 403,
404, 422) or a request the SDK refuses to send is `Rejected`; every other failure,
overload and rate limits included, is `ProviderUnavailable`, as for chat, so one retry
loop serves both. The client is made at the first Jev call: a gateway with no
`TYPESAFE_API_KEY` starts and serves chat, and rejects Jev calls.

**`typesafe-sdk>=0.7.5,<0.8`, from PyPI.** conda-forge has no build. Unlike LiteLLM's, its
wheels are pure Python with PyPI provenance, so a range suffices; the lock file fixes the
version, and a minor release, which may break a 0.x API, is taken by hand.

## Consequences

- Tests play Jev's HTTP API with an `httpx2` transport under the SDK, so the real provider
  runs in the shared suite. The SDK's own request checks and parsing run too.
- The SDK's default request timeout applies to each attempt; the gateway sets none.
- Jev's usage is priced by `Prices.tokens_cost` from the pinned table or the settings.
