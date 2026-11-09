---
search:
  exclude: true
---

# Rate limits and retries

`labllm.server.limits` holds each provider to its rate limits; `labllm.retry` decides when a
failed attempt is tried again. Five choices cost something if reversed.

**A provider's `ProviderUnavailable` means "try again".** Each provider maps its own errors:
`ProviderExhausted` for a used-up balance or cap, read from the body as well as the status
(any 402; OpenAI's 429 `insufficient_quota`; Anthropic's 400 about credit), `Rejected` for
a malformed or unauthorized request, and `ProviderUnavailable` with the status and
`Retry-After` for everything else. The retry loop reads only that shape, so it knows no
provider. The cost: a new provider's quota wording must be found and added by hand.

**Both gateways retry, by one rule.** The fake retries too, so the shared suite pins retries,
and a lab repo's tests see what a pass would. Waits double from one second up to a minute,
or follow `Retry-After`; a call gives up when its next attempt would start after the budget,
counted from its first failure.

**Waiting calls queue per provider, or per model when the settings name one.** First come,
first served; a retry keeps its call's place. A token bucket holds one second's worth of
each per-second limit. A waiting call holds no ceiling and no lock another provider needs.
Overload pauses every call to that provider; an empty reply retries only its own call.

**The rate estimate is four bytes a token, corrected by the provider's input count.** The
ceiling keeps its byte count, an upper bound. A wrong estimate costs a short burst or a
short idle, never money.

**An empty reply is a paid, failed attempt.** The provider returns it; the gateway records
its cost and tries again. A reply's `cost_usd` covers every attempt of its call.

## Consequences

- Replies and error bodies carry `attempts`, so a call that fails after retries reaches the
  engine with every request it sent. A reply with no count counts one, or none for a hit.
- `Clock.wait(condition, until)` joins the clock seam; `FakeClock.until_waiting` lets a test
  see calls waiting.
- The record gains `attempt` and `waited_s`.
- Jev's real reply to an empty balance is unknown; a 402 is assumed.
