---
search:
  exclude: true
---

# The call layer's wire

The client, `labllm.testing.FakeGateway` and the real gateway share `labllm.wire`. Two of
its choices change every cache key or every caller if reversed.

**The cache key keeps key order.** The key is the SHA-256 of the request form as compact
JSON, `ensure_ascii=False`, keys in the order written, computed by the gateway from the form
it decoded. Sorting the keys would let one schema written in two orders share a key, but a
schema's property order can change what a model writes, so a sorted key could replay an
answer to a different request. The cost: one schema written in two orders is paid twice.
`temperature` is sent as a float, so `1` and `1.0` share a key.

**Errors travel by reason, not by HTTP status.** An error body is
`{"error": <reason>, "message": <text>, "cost_usd": <paid>, "attempts": <sent>}`, where the
reason is the error class's `reason`.
The client raises the class the reason names and ignores the status. A response it cannot
read raises `GatewayUnreachable`, so a broken gateway stops a pass at once rather than
ending every unit unfinished one by one.

## Consequences

- `FakeGateway` is a transport: it takes and returns bytes like the HTTP gateway, so the
  client's encoding, error mapping and refused-connection retry run under it too.
- The client resends after any `ConnectionError` through its clock, a drop with no reply
  included, since a tunnel whose far end is down accepts the connection and then drops it.
  A resend is safe because of the gateway, not the client: one whose call is in flight waits
  for it, one whose call finished is a cache hit, and only one whose attempt died in a crash
  tries the provider again. A resend served as a hit shows no cost in the client's
  counters; the gateway's record and ceiling keep the real cost.
- The fake records hits and attempts at the provider. A call refused for its ceiling never
  reached one, so it is not an exchange.
