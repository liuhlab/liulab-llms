---
search:
  exclude: true
---

# A pass at scale

A pass runs many units at once, stops cleanly with calls in flight, and records each unit's
cost and time.

**A fixed set of threads takes keys in order.** `units_at_once` threads, 32 by default as in
the interface sketch, take keys from one iterator, so a pass over any number of keys holds
that many threads and no future per key. `u.each` works the same way within a unit, 8 at a
time by default. A kind's function must be safe to run on many threads.

**A stop blocks new calls, not calls in flight.** The first `Stopped` error a unit's call
meets stops the pass. From then on a call raises that error's class without being sent, no
unit starts, and the units not started are recorded unfinished in one transaction. A unit
that met the stop, by a failed or a blocked call, ends unfinished even if its code caught
the error and returned an answer. A unit whose calls were all answered keeps its ending.
Trusting the kind instead would record answers made without a model's reply; the cost is
that such a unit's paid calls are only replayed, free, on the rerun.

**Cost per unit comes from the replies.** `Unit` sums each reply's cost now, first cost,
cache flag and usage, and a failed call's paid cost from its error; the meter still gives the
pass's totals, each the sum of its units'. `requests` counts the requests sent to providers,
the gateway's retries included, from each reply's or error's `attempts`; a cache hit sends
none. `usage` adds the
providers' numbers key by key, nested keys too, replays included, since each provider names
its token counts its own way. Time runs on `Gateway.clock`, the client's clock, from the
first request to the ending, so a test's clock times it.

**Layout 2** adds the cost columns to `outcomes`, all nullable. A layout-1 row reads with
`cost` None: unknown, not zero.

## Consequences

- A unit's outcome, checks and cost commit together, so a stop leaves nothing half-recorded.
- An interrupt while a pass waits starts no further unit; units in flight are not waited
  for, and a unit never recorded has no outcome in that pass.
- If the gateway ever reports its retries per call, `requests` can add them without a layout
  change.
