---
search:
  exclude: true
---

# Off-peak only

`labllm.peak` decides, for the fake and the real gateway alike, whether an off-peak-only call
may go out now. Four choices cost something if reversed.

**The hours come from the price entry, read by our own code.** A model's off-peak hours are
the `off_peak_pricing` block of its entry in LiteLLM's pinned table, carried on
`Price.off_peak`; a test hands the fake the same block. `OffPeak` reads it as LiteLLM's cost
function does, since the plain install, and so the fake, has no LiteLLM. The cost: a pin that
changes how LiteLLM reads the block can split our hours from its prices, so moving the pin
means re-reading its `_is_off_peak`. A model with no off-peak hours is unaffected.

**Peak travels as an error with a time.** The reply is
`{"error": "peak_hours", "message": ..., "until": <epoch seconds>}`. The client waits on its
own clock until then, and at least a second after it sent, then sends again; the caller
never sees it. A client clock behind the gateway's sends early and is told again.

**A cache hit is answered in peak.** No call reaches the provider, so an off-peak-only pass
rerun in peak replays its finished units at once. A peak reply is not an exchange.

**The margin is a refusal window.** A call is refused when a peak hour starts within
`peak_margin_minutes` of it (5 by default), and `until` skips off-peak gaps shorter than the
margin. Whether DeepSeek bills by a call's start or end is not established; the margin keeps a
call clear of the boundary either way, unless the call outlasts it.

## Consequences

- `Meter.stop` wakes every call waiting out peak hours. A pass stops its meter when it stops,
  so a waiting unit ends unfinished with the stop's reason and sends nothing.
- `Clock.sleep_until(t, wake)` joins the clock seam. `FakeClock` moves to the later of now and
  `t`, so threads waiting for one moment wake together.
- A unit's time per pass includes its wait.
- The check runs once, before the ceiling hold. A gateway that later waits or retries before
  sending must check again before each attempt.
