---
search:
  exclude: true
---

# The gateway server

`labllm.server` answers `/chat` and `/jev`. Four choices cost something if reversed.

**LiteLLM 1.106.6, from PyPI.** The pin is the newest final release at least a week old
that no LiteLLM advisory names. conda-forge's newest, 1.106.0, builds the compiled part
from source, but the master-key advisory names it; that advisory is about the proxy, and
a rule with no exceptions is easier to keep. The cost: a wheel BerriAI compiled, with no
provenance. Moving the pin means re-reading the research note's points at the new tag.

**The standard library's `ThreadingHTTPServer`.** No package to pin, and a thread per call
suits blocking calls that wait at the gateway. The cost: no defence against slow or
hostile clients. That is safe only because the gateway listens on loopback behind SSH.
psycopg 3 with its pool talks to PostgreSQL; conda-forge's `postgresql` gives the tests,
and `--throwaway`, a server of their own.

**The provider seam is a function from request form to `Attempt`**: output, usage, cost
and whole response. Tests play it; `LiteLLMChat` is the real chat one, and takes a
stand-in for `litellm.completion` so provider settings are tested too. A provider prices
its own response, because LiteLLM's cost function needs LiteLLM's response; a stand-in's
cost is the test's word.

**One table, `exchanges`, holds the record.** Bodies are `json`, not `jsonb`, so a form
keeps its key order. The cache entry is a key's first successful row not retired, found
through a partial index. A call that misses the cache claims its key in memory; an identical
call waits for the claim, then reads the cache again, so a request is paid once. The cost: it
waits through the first call's retries. An admin retires a key with
`UPDATE exchanges SET retired_at = now() WHERE key = '…' AND retired_at IS NULL`. A meter's
spend is summed from its rows the first time the server sees it after a start.

## Consequences

- The ceiling's input estimate is the prompt's UTF-8 bytes, an upper bound for byte-level
  tokenizers. It is too high for a rate limit on input tokens.
- LiteLLM's usage object is recorded as the provider's usage. For DeepSeek it carries
  DeepSeek's own fields; for Claude LiteLLM rewrites the input count and keeps no raw copy.
- An empty reply is an error, never a cache entry.
- A later column is one more `ADD COLUMN IF NOT EXISTS` at the end of `record.SCHEMA`.
