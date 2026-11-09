---
search:
  exclude: true
---

# The gateway server

`labllm.server` answers `/chat` and `/jev`. Four choices cost something if reversed.

**LiteLLM 1.106.6, from PyPI.** The pin is a final release, at least a week old, that no
advisory names. It moves only for a model or fix the gateway needs, or an advisory against
it. conda-forge's 1.106.0 builds from source, but an advisory about LiteLLM's proxy names
it, and a rule without exceptions is easier to keep. The cost: a wheel BerriAI compiled,
with no provenance. A move re-checks, at the new tag, the points in
[the call layer's research resolution](https://github.com/liuhlab/liulab-llms/issues/2).

**The standard library's `ThreadingHTTPServer`.** No package to pin, and a thread per call
suits blocking calls that wait at the gateway. The cost: no defence against slow or
hostile clients, safe only because the gateway listens on loopback behind SSH. psycopg 3
talks to PostgreSQL; conda-forge's `postgresql` gives the tests, and `--throwaway`, a
server of their own.

**The provider seam is a function from request form to `Attempt`**: output, usage, cost
and whole response. Tests play it; `LiteLLMChat`, the real chat one, takes a stand-in for
`litellm.completion` so provider settings are tested too. A provider prices its own
response, because LiteLLM's cost function needs LiteLLM's response.

**One table, `exchanges`, holds the record.** Bodies are `json`, not `jsonb`, so a form
keeps its key order. The cache entry is a key's first finished successful row not retired.
A call that misses the cache claims its key in memory; an identical call waits for the
claim, then reads the cache again, so a request is paid once, at the cost of waiting
through the first call's retries. An admin retires a key with
`UPDATE exchanges SET retired_at = now() WHERE key = '…' AND retired_at IS NULL`. An
attempt's row is added when it starts and finished when it ends. A meter's spend is summed
from its rows when the server first sees it, an unfinished row at its reservation, so a
crash mid-call keeps the ceiling.

## Consequences

- The ceiling's input estimate is the UTF-8 bytes of everything sent as input, as JSON,
  the schema included: a bound on a byte-level tokenizer, too high for a rate limit.
- The record keeps the provider's own body and usage, from the per-call `logger_fn` hook,
  which sees the body before LiteLLM rewrites Claude's usage. LiteLLM's stand in only when
  that body is not JSON with a usage object.
- An empty reply is an error, never a cache entry.
- A later column is one more statement at the end of `record.SCHEMA`.
