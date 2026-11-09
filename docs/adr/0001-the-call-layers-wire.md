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
`{"error": <reason>, "message": <text>}`, where the reason is the error class's `reason`.
The client raises the class the reason names and ignores the status. A response it cannot
read raises `GatewayUnreachable`, so a broken gateway stops a pass at once rather than
ending every unit unfinished one by one.

## Consequences

- `FakeGateway` is a transport: it takes and returns bytes like the HTTP gateway, so the
  client's encoding, error mapping and refused-connection retry run under it too.
- The client retries any `ConnectionError` through its clock, since a tunnel whose far end
  is down accepts the connection and then drops it rather than refusing it.
- The fake records hits and attempts at the provider. A call refused for its ceiling never
  reached one, so it is not an exchange.
