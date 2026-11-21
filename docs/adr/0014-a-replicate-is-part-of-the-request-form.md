---
search:
  exclude: true
---

# A replicate is part of the request form

A caller that repeats a request on purpose, to measure how often a model's answer changes,
got the first answer back from the cache every time. Two choices cost something if reversed.

**The replicate number enters the request form, past the first.** `Meter.chat(...,
replicate=n)` adds `"replicate": n` at the end of the form when `n` is above 1, so each
replicate has its own cache key and a rerun replays each as first answered. Replicate 1 is
the plain request, so every key made before stays, and a pass's answer serves as another
caller's first replicate without being paid twice. A flag that skips the cache was turned
down: a rerun would pay every repeat again and replay none. The cost: renaming or moving
the field changes the key of every replicate past the first.

**The gateway never sends it.** The provider mapping builds each provider's request from
named fields, so the replicate reaches the cache key and the record but not the provider.
A gateway older than replicates serves them unchanged. The cost: any field a client adds to
a form splits the cache the same way, and nothing checks it.

## Consequences

- Only chat takes a replicate. A Jev answer is a judgment with probabilities; a repeat
  there waits for a caller who needs one.
- A model run at temperature 0 may give every replicate the same answer. That is the
  model, not the cache.
