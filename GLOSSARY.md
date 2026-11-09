# Glossary

The words for model calls through the lab gateway, and for the work run over many units.

## Glossary

### Ceiling

A meter's limit in dollars, required and with no default. The gateway refuses a call whose
largest possible cost (estimated input plus the full output allowance), added to what the
meter has spent and holds for calls in flight, would cross it. A cache hit never counts.
_Avoid_: budget, cap, limit

### Exchange

One attempt at a provider, retries each their own, recorded whole in the gateway's record:
the request, the response or error, usage, cost, and the person, repo, meter and tag. A cache
hit adds an exchange with no bodies.
_Avoid_: transcript, log entry

### Gateway

The lab's one service that sends every model call: it holds the provider keys, keeps the
record, replays any request it has answered, and enforces each meter's ceiling. `Gateway` is
also the library's client for it.
_Avoid_: proxy

### Meter

A group of calls under one ceiling, with its own identity on the gateway. Its label is for
people, so two meters with one label never share a ceiling. A pass opens one meter.
_Avoid_: session, budget

### Request form

What decides a call's answer, as `labllm.wire` builds it: provider, exact model name,
settings, the messages or Jev's description and questions, and the output schema. Its hash
is the cache key; labels, the tag and the ceiling are never in it.
_Avoid_: payload, prompt
