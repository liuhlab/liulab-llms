# What LiteLLM gives the call layer

Query date: 2026-11-08. This note answers the seven points of the research ticket from
primary sources only:

- **LiteLLM's source** at release `v1.108.3`, commit
  [`b7d579f`](https://github.com/BerriAI/litellm/tree/b7d579fab2818968322ef96e5b8e98dc8178fafc),
  the newest stable release on PyPI on the query date. Every LiteLLM link below points at that
  commit, with line numbers.
- **LiteLLM's docs** at <https://docs.litellm.ai>, read on the query date.
- **LiteLLM's PyPI release history** (the JSON and simple-index APIs) and its GitHub releases,
  tags, commits and security advisories.
- **TypeSafe's Python SDK**, `typesafe-sdk` `v0.7.5`, commit
  [`f36e685`](https://github.com/typesafe-ai/typesafe-sdk-python/tree/f36e68549ceca3191e5791e5f99ea1baa1a258b5),
  and TypeSafe's SDK docs.
- **DeepSeek's API docs**, only where a LiteLLM behaviour has to be checked against what
  DeepSeek accepts.

Nothing was installed or run. Source was read as downloaded archives. No API key was used and no
call was made.

Each claim is marked **read** (seen in the source or doc cited), **inferred** (follows from what
was read, not seen directly) or **not established**.

Terms used below: the **SDK path** is calling `litellm.completion`, `litellm.systemone` and the
like directly. The **Router** is LiteLLM's load-balancing class (`litellm.Router`), which runs in
the same process; it is not the proxy server. **Retry-After** is the HTTP header a server sends
to say how long to wait before trying again. A **529** is the "overloaded" status that Jev and
Claude send.

## Answer in brief

- **Jev probabilities: untouched.** LiteLLM's `systemone` call parses each probability as a
  plain float and passes it on unchanged. It does change the answer set in three small ways (a
  missing score level becomes 0.0, an unanswered question is dropped, choice keys are mapped
  back), and it refuses more than 128 questions in one request.
- **Jev 529: not retried on the SDK path.** A `systemone` call is one HTTP request. Only the
  Router's async `asystemone` retries a 529, honouring Retry-After. TypeSafe's own SDK retries
  529 by default.
- **Retries can all be turned off.** DeepSeek, Claude and Jev make one attempt by default on the
  SDK path. OpenAI calls go through the OpenAI SDK, which retries twice unless `max_retries=0`.
  LiteLLM's own opt-in `num_retries` ignores Retry-After; only the Router honours it.
- **No usable limit on calls at once.** The only limit is the Router's per-deployment
  `max_parallel_requests`, and it rejects an extra call with a 429 rather than queueing it.
- **Prices: present and mostly right.** The bundled table has DeepSeek's cache-hit and
  half-price off-peak rates, Jev's $0.042 per million input tokens, and current OpenAI and Claude
  rates. It misses DeepSeek's holiday off-peak days. By default LiteLLM downloads the newest
  table from GitHub at import, so prices are not fixed by the pin.
- **Usage: present, but rewritten for Claude.** For Claude, LiteLLM's input count includes cache
  reads and writes, and its thinking-token count can be an estimate.
- **DeepSeek thinking off: works.** Thinking *effort* is dropped. DeepSeek accepts no JSON
  schema, only JSON mode; LiteLLM passes a schema through anyway.
- **The pin:** `litellm==1.108.3`, the release this note read, once it is a week old. No release
  since April 2026 carries PyPI provenance, and every wheel since August 2026 contains compiled
  code.

## 1. Jev: probabilities and the 529

### LiteLLM's TypeSafe client

- **Read:** `litellm.systemone(model, state, questions, ...)` and its async twin `asystemone`
  build one request and hand it to `send` / `asend`
  ([`systemone/main.py` L64–133](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/systemone/main.py#L64-L133)).
  Those make exactly one HTTP POST through LiteLLM's httpx handler
  ([`decisions/call.py` L740–785](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/decisions/call.py#L740-L785);
  [`llm_http_handler.py` L1535–1640](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/custom_httpx/llm_http_handler.py#L1535-L1640)).
  The TypeSafe config only sets the base URL (`https://api.typesafe.ai`), the key variable
  `TYPESAFE_API_KEY`, and image handling
  ([`typesafe/decisions/transformation.py` L78–110](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/typesafe/decisions/transformation.py#L78-L110)).
- **Read: probabilities are passed through unchanged.** The reply is parsed by Pydantic into
  models whose probability fields are plain `float`s, with no rounding, clamping or
  renormalising: `noul: float`, `confidence: float`, `probabilities: Mapping[str, float]`
  ([`types/decisions.py` L203–226](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/types/decisions.py#L203-L226)).
  The values are copied into an internal form and back out again without arithmetic
  ([`base_llm/decisions/transformation.py` L331–453](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/base_llm/decisions/transformation.py#L331-L453)).
- **Read: three changes to the answer set, none to the numbers.**
  - A score level the reply leaves out is filled in with probability `0.0`
    ([L344–357](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/base_llm/decisions/transformation.py#L344-L357)).
  - A question with no answer, or with an answer of an unexpected type, becomes a refusal and is
    left out of `answers`
    ([L360–373](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/base_llm/decisions/transformation.py#L360-L373),
    [L434–440](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/base_llm/decisions/transformation.py#L434-L440)).
  - Choice keys are mapped back to the caller's own choice values
    ([L331–341](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/base_llm/decisions/transformation.py#L331-L341)).
- **Read: at most 128 questions per request.** LiteLLM rejects a request with more before any
  call ([`types/decisions.py` L13, L76](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/types/decisions.py#L13-L76);
  [docs: Decisions](https://docs.litellm.ai/docs/decisions)). TypeSafe itself sets no limit on
  the number of questions, only on tokens (seqforge's provider-limits note, citing
  <https://docs.typesafe.ai/models>).
- **Read: no retry on the SDK path.** The `@client` wrapper that every LiteLLM call passes
  through retries only `completion`/`acompletion` and `responses`/`aresponses`
  ([`utils.py` L1970–2030](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L1970-L2030),
  [L2295–2348](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L2295-L2348)).
  So a 529 from Jev is raised to the caller at once. The one hidden extra attempt: the async HTTP
  client retries once, on a fresh connection, when the connection itself fails
  ([`http_handler.py` L855–868](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/custom_httpx/http_handler.py#L855-L868)).
- **Read: the Router's async path does retry a 529.** `router.asystemone` goes through the
  Router's fallback-and-retry loop
  ([`router.py` L2424–2427](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L2424-L2427),
  [L6393–6410](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L6393-L6410),
  [L5651–5683](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L5651-L5683),
  [L8306–8330](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L8306-L8330)).
  - A 529 from TypeSafe becomes `APIError(status_code=529)` with the response headers kept
    ([`exception_mapping_utils.py` L2405–2412](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/litellm_core_utils/exception_mapping_utils.py#L2405-L2412),
    [L2793–2806](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/litellm_core_utils/exception_mapping_utils.py#L2793-L2806)).
  - It is retried, because the Router retries 408, 409, 429 and every status of 500 or above
    ([`utils.py` L7614–7642](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L7614-L7642)).
  - It is retried twice by default
    ([`router.py` L1398–1403](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L1398-L1403)).
    The wait is as in section 2.
  - The sync `router.systemone` makes one call and does not retry
    ([L7358–7363](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L7358-L7363),
    [L6412–6470](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L6412-L6470)).

### TypeSafe's own SDK

- **Read: probabilities untouched.** Answers are strict, frozen Pydantic models with `float`
  probabilities
  ([`response_types.py` L22–80](https://github.com/typesafe-ai/typesafe-sdk-python/blob/f36e68549ceca3191e5791e5f99ea1baa1a258b5/src/typesafe_sdk/_core/response_types.py#L22-L80);
  wire schema [`models.py` L109–117](https://github.com/typesafe-ai/typesafe-sdk-python/blob/f36e68549ceca3191e5791e5f99ea1baa1a258b5/src/typesafe_sdk/_schemas/models.py#L109-L117)).
  An answer of a type this SDK version does not know is dropped with a warning; the raw HTTP
  response stays reachable
  ([L97–114](https://github.com/typesafe-ai/typesafe-sdk-python/blob/f36e68549ceca3191e5791e5f99ea1baa1a258b5/src/typesafe_sdk/_core/response_types.py#L97-L114)).
- **Read: 529 is retried by default.** The default `RetryPolicy` retries 408, 429 and 500–599,
  plus connection errors and timeouts.
  - It makes up to 2 retries. The wait starts at 0.5 s and doubles to at most 5 s, with up to
    25% jitter.
  - It honours `Retry-After` and `retry-after-ms`, within a 30 s total budget per call.
  - `RetryPolicy(max_retries=0)` turns retries off. Passing `retry=None` gives the *default*
    policy, not no retries.
  - Sources:
    [`retry.py` L52–131](https://github.com/typesafe-ai/typesafe-sdk-python/blob/f36e68549ceca3191e5791e5f99ea1baa1a258b5/src/typesafe_sdk/_core/retry.py#L52-L131);
    [docs: Retries](https://docs.typesafe.ai/sdk/python/api/retries).
- **Read:** the SDK depends on `httpx2`, `pydantic`, `tenacity` and `truststore`, and needs
  Python 3.10 or newer
  ([`pyproject.toml`](https://github.com/typesafe-ai/typesafe-sdk-python/blob/f36e68549ceca3191e5791e5f99ea1baa1a258b5/pyproject.toml#L14-L45)).

### What this means for the map's Jev rule

- **Inferred:** the map says to use LiteLLM's Jev client only if it returns each probability
  untouched and retries 529. It passes the first test. It passes the second only through the
  Router's async path.
- **Inferred:** if the call layer turns every library's retries off and retries itself (section
  2), the second test no longer decides anything. Both clients then pass. LiteLLM's client adds
  a cost figure and one exception family shared with the other providers. It adds a 128-question
  cap.

## 2. Retries

### What each provider path does on the SDK path, by default

| Provider (LiteLLM route) | How the request is sent | Retries by default | Turned off by |
| --- | --- | --- | --- |
| DeepSeek (`deepseek/...`) | LiteLLM's own httpx handler ([`main.py` L1477–1525](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/main.py#L1477-L1525)) | none for any status ([`llm_http_handler.py` L542–566](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/custom_httpx/llm_http_handler.py#L542-L566) retries only a provider-specific 422 rewrite) | already off |
| Claude (`anthropic/...`) | LiteLLM's own httpx handler; the Anthropic SDK is not used ([`anthropic/chat/handler.py`](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/anthropic/chat/handler.py#L70-L71)) | none found | already off |
| OpenAI (`openai/...`) | the OpenAI Python SDK | 2, by the OpenAI SDK ([`openai.py` L785](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/openai/openai.py#L785); [`constants.py` L125](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/constants.py#L125)) | `max_retries=0` or `num_retries=0` per call ([`main.py` L5459–5561](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/main.py#L5459-L5561)) |
| Jev (`typesafe/...`, `systemone`) | LiteLLM's own httpx handler | none (section 1) | already off |

- **Read** for the DeepSeek, Claude and Jev rows. For the OpenAI row, the default of 2 is read;
  that `max_retries=0` reaches the OpenAI client is **inferred**
  ([`openai.py` L392–397](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/openai/openai.py#L392-L397)
  sets it when given).
- **Read:** the async HTTP client's one reconnect on a failed connection (section 1) applies on
  every route that uses it.

### LiteLLM's opt-in retries on the SDK path

- **Read:** `num_retries` (per call) or `litellm.num_retries` (process-wide, `None` by default
  ([`__init__.py` L548](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/__init__.py#L548)))
  retries only `completion` and `responses` calls, on any `openai.APIError` subclass
  ([`utils.py` L1970–2030](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L1970-L2030)).
  - **The wait.** Sync calls retry with no wait at all. Async calls wait exponentially (1 s up to
    10 s) only after a 429, and otherwise not at all. `Retry-After` is ignored
    ([`main.py` L6245–6294](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/main.py#L6245-L6294)).
  - **Tenacity is not installed by LiteLLM.** These retries need the `tenacity` package, which
    LiteLLM lists only in its CI group, not as a dependency
    ([`pyproject.toml` L263–268](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/pyproject.toml#L263-L268)).
  - **The global switch resets itself.** A failed sync `completion` sets the process-wide
    `litellm.num_retries` back to `None`
    ([`utils.py` L1983](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L1983)).
  - The docs say only "use tenacity retries"
    ([docs: Reliability](https://docs.litellm.ai/docs/completion/reliable_completions)).

### The Router's retries

- **Read:** the Router retries 408, 409, 429 and every status of 500 or above, twice by default
  (section 1). It sets `max_retries=0` on the inner call, so the OpenAI SDK does not retry as
  well ([`main.py` L5557–5559](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/main.py#L5557-L5559)).
- **Read: the wait honours Retry-After, within limits.**
  - A `Retry-After` between 0 and 60 seconds is obeyed, plus up to 0.75 s of jitter.
  - Otherwise the wait is 0.5 s doubled per retry, capped at 8 s
    ([`utils.py` L7680–7707](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L7680-L7707);
    [`constants.py` L549–551](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/constants.py#L549-L551);
    [`router.py` L8647–8690](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L8647-L8690)).
  - The header is read as whole seconds or an HTTP date. `retry-after-ms` is ignored
    ([`utils.py` L7646–7677](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L7646-L7677)).
  - The docs describe only `retry_after`, a minimum wait, not the header
    ([docs: Routing](https://docs.litellm.ai/docs/routing)).
- **Read:** a 401 or 403 is retried only when the model group has more than one deployment, and
  a 429 is not retried when fallbacks exist and no deployment is healthy
  ([`router.py` L8552–8616](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L8552-L8616)).

### Can the call layer count every attempt itself?

- **Inferred: yes.** Pass `max_retries=0` on every call, leave `num_retries` unset and do not use
  the Router. Then each LiteLLM call is one attempt, except for the silent reconnect after a
  failed connection. A failed connection means no request reached the provider, so it is
  probably not billed (**not established**).
- **Read:** every error LiteLLM raises carries the provider's response headers as
  `litellm_response_headers`
  ([`exception_mapping_utils.py` L2793–2806](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/litellm_core_utils/exception_mapping_utils.py#L2793-L2806);
  [`llm_http_handler.py` L6355–6418](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/custom_httpx/llm_http_handler.py#L6355-L6418)).
  So the call layer can read `Retry-After` and `retry-after-ms` itself.

## 3. A limit on calls at once, per provider, without the proxy

- **Read: the SDK path has none.** The only limiter is the Router's `max_parallel_requests` per
  deployment.
  - Where it is not set, it falls back to the deployment's `rpm`, then `tpm` / 1000 × 6, then the
    Router's `default_max_parallel_requests`
    ([`utils.py` L5529–5569](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L5529-L5569);
    [`client_initalization_utils.py` L53–80](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router_utils/client_initalization_utils.py#L53-L80)).
  - It does not queue: a call that arrives while every slot is busy fails at once with a 429
    `RateLimitError`
    ([L16–50](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router_utils/client_initalization_utils.py#L16-L50);
    [`router.py` L9394–9408](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L9394-L9408)).
  - The docs agree: "There is no wait queue". They add that "Earlier versions queued instead of
    rejecting" ([docs: Routing](https://docs.litellm.ai/docs/routing)).
- **Inferred:** that local 429 then enters the Router's own retry loop and waits up to 8 s, so it
  behaves like polling, not a queue. The count lives in one Router object, so it covers one
  process only.
- **Inferred:** the call layer needs its own limiter per provider: a semaphore that makes extra
  calls wait, with backoff when the provider signals overload.

## 4. Cost per call and the price table

### What the bundled table says

Prices per million tokens, from `model_prices_and_context_window.json` in the release. The
off-peak rates apply on Monday to Friday at 00:00–01:00, 04:00–06:00 and 10:00–24:00 UTC, and
all day on Saturday and Sunday.

| Model key | Input | Cache-hit input | Output | Off-peak (input / cache hit / output) | Line |
| --- | --- | --- | --- | --- | --- |
| `deepseek-flash`, `deepseek/deepseek-flash` | $0.30 | $0.006 | $1.20 | $0.15 / $0.003 / $0.60 | [L62543](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/model_prices_and_context_window.json#L62543-L62596), [L62759](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/model_prices_and_context_window.json#L62759) |
| `deepseek-v4-flash` (legacy name) | $0.30 | $0.006 | $1.20 | same as Flash | [L62597](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/model_prices_and_context_window.json#L62597) |
| `deepseek-v4-pro`, `deepseek/deepseek-v4-pro` | $1.32 | $0.044 | $3.96 | $0.66 / $0.022 / $1.98 | [L62705](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/model_prices_and_context_window.json#L62705), [L62921](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/model_prices_and_context_window.json#L62921-L62945) |
| `typesafe/jev-1.14.0` (also `jev-1.13.0`, `jev-latest`, `jev-preview`) | $0.042 | none listed | $0 | none | [L72697](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/model_prices_and_context_window.json#L72697-L72705) |
| `gpt-5.5` | $5 | $0.50 | $30 | none; batch, flex, priority and over-272k rates also listed | [L37758](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/model_prices_and_context_window.json#L37758) |
| `claude-opus-4-8` | $5 | $0.50 (writes $6.25, or $10 for 1 h) | $25 | none; batch half price | [L16492](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/model_prices_and_context_window.json#L16492) |
| `claude-opus-5-5` | $4 | $0.20 | $20 | none; batch half price | [L16395](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/model_prices_and_context_window.json#L16395) |

- **Read:** the table above. Its DeepSeek, Jev and Claude input, cache-hit, output, off-peak and
  batch figures match the provider prices recorded in seqforge's provider-limits note
  (DeepSeek's pricing page, TypeSafe's models page, Anthropic's pricing page, all read on the
  same date). OpenAI's figures were not checked against OpenAI's page. The off-peak windows are exactly the
  complement of DeepSeek's peak hours, 01:00–04:00 and 06:00–10:00 UTC on weekdays.
- **Read: Chinese public holidays are missing.** DeepSeek prices them off-peak all day, and the
  table has no holiday rule. **Inferred:** LiteLLM charges peak rates on a weekday holiday, which
  overstates cost; that errs safe for a spend ceiling.
- **Read: how the cost function prices a DeepSeek call.** Cache-hit tokens are charged at the
  cache-hit rate and the rest of the input at the input rate
  ([`llm_cost_calc/utils.py` L1174](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/litellm_core_utils/llm_cost_calc/utils.py#L1174)).
  Thinking tokens are charged at the output rate unless the model lists a separate one
  ([L536–545](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/litellm_core_utils/llm_cost_calc/utils.py#L536-L545)).
- **Read: off-peak is decided by the clock when the cost is computed**, in UTC, not by when the
  request was sent
  ([L506–532](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/litellm_core_utils/llm_cost_calc/utils.py#L506-L532),
  [L573–593](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/litellm_core_utils/llm_cost_calc/utils.py#L573-L593);
  [`_internal_context.py` L117–129](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/_internal_context.py#L117-L129)).
  The public `litellm.completion_cost` takes no time argument
  ([`cost_calculator.py` L1473–1503](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/cost_calculator.py#L1473-L1503)).
  Only the lower-level `generic_cost_per_token` takes a `current_time`
  ([`llm_cost_calc/utils.py` L1392–1415](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/litellm_core_utils/llm_cost_calc/utils.py#L1392-L1415)),
  and the context manager that fixes the moment, `pinned_billing_time`, sits in the private
  module `litellm._internal_context`.
  **Not established:** whether DeepSeek bills by the time a request starts or ends. A call that
  crosses 01:00 or 06:00 UTC may be priced at the other rate.
- **Read:** Jev responses are priced by the same cost function, from the input-token count
  ([`cost_calculator.py` L587–600](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/cost_calculator.py#L587-L600),
  [L1158–1159](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/cost_calculator.py#L1158-L1159),
  [L1205](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/cost_calculator.py#L1205)).
- **Read:** LiteLLM's own budget, `litellm.max_budget`, is one process-wide total held in memory
  and checked before each call
  ([`utils.py` L1809–1813](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L1809-L1813)).
  **Inferred:** it reserves nothing for calls in flight and resets on restart.

### How current the table is kept

- **Read: by default the table comes from GitHub, not the pinned release.** At import, LiteLLM
  downloads the table from the `main` branch on GitHub (5 s timeout). It falls back to the copy
  bundled in the wheel only if the download fails. Setting
  `LITELLM_LOCAL_MODEL_COST_MAP=True` forces the bundled copy
  ([`__init__.py` L430–433, L572](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/__init__.py#L430-L433);
  [`get_model_cost_map.py` L1–9, L651–700](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/litellm_core_utils/get_model_cost_map.py#L651-L700)).
  **Inferred:** without that setting, two runs on the same pin can price the same call
  differently, and every import makes a network request, tests included.
- **Read (GitHub API):** the table file had at least 100 commits between 2026-10-08 and
  2026-11-08, some of them automated price syncs.
- **Read: DeepSeek's off-peak prices arrived five weeks late.** They took effect on 2026-08-16
  (seqforge's provider-limits note). They entered the table with
  [BerriAI/litellm#41960](https://github.com/BerriAI/litellm/pull/41960), merged 2026-09-19.
  **Inferred:** a provider price change can take weeks to reach the table.

## 5. Usage per response

| Provider | Input | Cached input | Output | Thinking |
| --- | --- | --- | --- | --- |
| DeepSeek | `usage.prompt_tokens` (DeepSeek's own: hit + miss) | `prompt_tokens_details.cached_tokens`, set from DeepSeek's `prompt_cache_hit_tokens`; the raw `prompt_cache_hit_tokens` and `prompt_cache_miss_tokens` are also kept | `completion_tokens` | `completion_tokens_details.reasoning_tokens`, DeepSeek's own field |
| OpenAI | `prompt_tokens` | `prompt_tokens_details.cached_tokens` | `completion_tokens` | `completion_tokens_details.reasoning_tokens` |
| Claude | `prompt_tokens` = Anthropic's `input_tokens` **plus** cache reads **plus** cache writes | `cached_tokens` = cache reads; `cache_creation_tokens` = cache writes | `completion_tokens` | reported thinking tokens if Anthropic sends them; otherwise an **estimate** from tokenising the returned thinking text; otherwise unknown or 0 |
| Jev | `input_tokens` | `cached_tokens`, `cache_write_tokens` (LiteLLM's fields) | `output_tokens` | none |

- **Read for DeepSeek:**
  [`types/utils.py` L2008–2012, L2058–2063](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/types/utils.py#L2008-L2063);
  every field the provider sends is also set on the usage object
  ([L2061–2062](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/types/utils.py#L2061-L2062)).
  DeepSeek documents `prompt_tokens` as hit plus miss, and `completion_tokens_details.reasoning_tokens`
  as its thinking count ([DeepSeek: Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion)).
- **Inferred for OpenAI:** the OpenAI SDK's usage object passes through unchanged. It was not
  traced line by line.
- **Read for Claude:**
  [`anthropic/chat/transformation.py` L2743–2790](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/anthropic/chat/transformation.py#L2743-L2790),
  [L2656–2687](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/anthropic/chat/transformation.py#L2656-L2687).
- **Read for Jev:**
  [`base_llm/decisions/transformation.py` L376–390](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/base_llm/decisions/transformation.py#L376-L390).
  TypeSafe's wire schema has only `input_tokens` and `output_tokens`, and its SDK keeps only
  those two
  ([`models.py` L187–197](https://github.com/typesafe-ai/typesafe-sdk-python/blob/f36e68549ceca3191e5791e5f99ea1baa1a258b5/src/typesafe_sdk/_schemas/models.py#L187-L197);
  [`response_types.py` L83–91](https://github.com/typesafe-ai/typesafe-sdk-python/blob/f36e68549ceca3191e5791e5f99ea1baa1a258b5/src/typesafe_sdk/_core/response_types.py#L83-L91)).
- **Inferred:** the meter should record the provider's raw usage next to LiteLLM's normalised
  one. LiteLLM's Claude input count is not Anthropic's, and its Claude thinking count can be an
  estimate. Billing is not affected: Claude's output count already includes thinking.

## 6. Settings

### Turning DeepSeek's thinking off

- **Read: off works.** `thinking={"type": "disabled"}` or `reasoning_effort="none"` sends
  `{"thinking": {"type": "disabled"}}`
  ([`deepseek/chat/transformation.py` L22–70](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/deepseek/chat/transformation.py#L22-L70)).
  That is DeepSeek's documented switch
  ([DeepSeek: Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)).
- **Read: the effort level is dropped.** Any other `reasoning_effort` becomes
  `{"thinking": {"type": "enabled"}}` with no effort sent, and a `thinking` budget is dropped
  (same lines). DeepSeek takes `reasoning_effort` of `low`, `high` or `max`, with `high` the
  default ([DeepSeek: Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion)).
  So `low` and `max` cannot be reached through LiteLLM's own parameters.
- **Inferred:** `extra_body={"reasoning_effort": "max"}` would reach DeepSeek, because
  `extra_body` is merged into the request body after LiteLLM's mapping
  ([`llm_http_handler.py` L723, L760–770](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/custom_httpx/llm_http_handler.py#L760-L770)).

### Structured output to a JSON schema

- **OpenAI (read):** `response_format` is passed through unchanged, so a JSON schema reaches
  OpenAI's own structured output
  ([`gpt_transformation.py` L196–197](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/openai/chat/gpt_transformation.py#L196-L197)).
- **Claude (read):** Opus 4.8 and Opus 5.5 are marked `supports_native_structured_output` in the
  table. For them, a JSON-schema `response_format` becomes Anthropic's native `output_format`;
  other Claude models get a forced tool call instead
  ([`anthropic/chat/transformation.py` L489–492, L1880–1902](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/llms/anthropic/chat/transformation.py#L1880-L1902)).
- **DeepSeek (read): no schema is enforced.**
  - DeepSeek accepts `response_format` of only `text` or `json_object`. JSON mode asks for the
    word "json" in the prompt and can return blank content
    ([DeepSeek: Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion);
    [DeepSeek: JSON Output](https://api-docs.deepseek.com/guides/json_mode)).
  - LiteLLM passes any `response_format` through, and its table still marks `deepseek-flash` and
    `deepseek-v4-pro` as `supports_response_schema: true`.
  - **Not established:** what DeepSeek does when sent a `json_schema` format.
- **Read:** LiteLLM has an opt-in check of the reply against the schema on the client,
  `litellm.enable_json_schema_validation`
  ([`__init__.py` L387](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/__init__.py#L387);
  [`utils.py` L1661–1670](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L1661-L1670)).
  **Inferred:** the call layer keeps its own Pydantic validation of every structured answer.

## 7. The pin

- **Read (PyPI, 2026-11-08):**
  - The newest stable release is **1.108.3**, uploaded 2026-11-07. Releases 1.82.7 and 1.82.8
    are no longer on PyPI.
  - Since 2026-03-24 there have been 247 uploads: 156 final releases and 91 dev and
    release-candidate builds.
  - The finals span 25 minor lines, 1.83 to 1.108; 1.107 never had a final release.
  - That is about five final releases a week overall, and seven to eight a week over the last
    eight weeks.
- **Read: several lines are patched at once.** A new minor line starts about once a week, and
  older lines keep getting patch releases for three to five weeks. For example, 1.103.0 came out
  on 2026-09-27 and 1.103.10 on 2026-10-27.
- **Read: no provenance since April.** PyPI provenance attestations exist for 1.83.0 to 1.83.14
  (2026-03-31 to 2026-04-26) and for no release since 2026-04-29. An attestation is a signed
  record of which repository commit and workflow built the file. The publishing workflow is not
  in the public source tree.
- **Read: compiled code in every wheel.** Since 1.96.2 (2026-08-11) each wheel carries a
  compiled Rust extension, built with maturin
  ([`pyproject.toml` L309–321](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/pyproject.toml#L309-L321)).
  **Inferred:** nothing ties the installed wheel to the source read for this note.
- **Read: both lab platforms have wheels.** 1.108.3 ships wheels for macOS arm64
  (`macosx_11_0_arm64`) and Linux x86-64 (`manylinux_2_28_x86_64`, glibc 2.28 or newer), and
  requires Python 3.10 to 3.14
  ([`pyproject.toml` L6](https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/pyproject.toml#L6)).
- **Read:** the core install pulls in `openai`, `httpx`, `aiohttp`, `tokenizers`,
  `huggingface-hub`, `tiktoken`, `boto3`, `jinja2` and `pydantic`, among others (PyPI metadata).
- **Read: the advisories name proxy features.** LiteLLM has published 18 GitHub security
  advisories, the first on 2026-04-03. Every title names a proxy-server feature: its endpoints,
  login, guardrails or MCP gateway. **Inferred:** SDK-only use is outside them.
- **Recommendation (inferred): pin `litellm==1.108.3` exactly**, locked with hashes in
  `pixi.lock`.
  - Every source claim in this note was read at that tag, so it holds for that version and no
    other.
  - It was one day old on the query date. Lock it once it is a week old, and only if it is
    neither yanked nor named in an advisory.
  - Set `LITELLM_LOCAL_MODEL_COST_MAP=True` before import, so prices come from the pinned
    release.
  - Before any later bump, re-read the points in sections 1 to 6 at the new tag.

## What liulab-llms must build itself

1. **A retry loop that counts every attempt.** Turn LiteLLM's retries off (`max_retries=0`, no
   `num_retries`, no Router). Treat 408, 429 and 5xx, including 529, as worth retrying. Read
   `Retry-After` (seconds or date) and `retry-after-ms` from `litellm_response_headers`.
2. **A limit on calls at once per provider that queues** rather than rejects, with backoff on
   overload.
3. **The response cache on disk** (already decided on the map).
4. **The meter and ceiling.**
   - Per attempt, record the provider's raw usage and LiteLLM's computed cost.
   - Price from the bundled table and stamp the LiteLLM version.
   - Decide DeepSeek's holiday and boundary rule. LiteLLM prices off-peak by the moment it
     computes the cost, and its public cost function cannot be told another time.
   - Reserve budget for calls in flight.
5. **DeepSeek's thinking setting, sent and recorded.** "Off" goes through LiteLLM; an effort
   level needs `extra_body`.
6. **Validation of every structured answer** against its schema, since DeepSeek cannot enforce
   one.
7. **Jev requests over 128 questions**, split or refused, if a unit ever needs that many.

## Not established

- What DeepSeek does when sent `response_format` of type `json_schema`: an error, or JSON mode,
  or plain text.
- Whether DeepSeek bills off-peak by a request's start time or end time.
- Whether a connection that fails before any request reaches the provider is ever billed.
- Whether LiteLLM exposes every provider's raw usage object on its response; only DeepSeek's
  extra fields were seen kept.
- Whether Anthropic reports a thinking-token count for current Claude models, which decides
  whether LiteLLM's figure is reported or estimated.
- That `max_retries=0` reaches the OpenAI client on every OpenAI code path; one path was read.
- Whether the table's OpenAI prices match OpenAI's own pricing page; it was not read.
- Why PyPI provenance stopped after 1.83.14, and whether the 1.108.3 wheels match the tag.
- How quickly the malicious 1.82.7 and 1.82.8 uploads were removed. They are gone from PyPI, so
  their history is not in its API.

## Method

LiteLLM's source at `v1.108.3` and TypeSafe's SDK at `v0.7.5` were downloaded as GitHub archives
into empty folders and read with text search. No code from them was run. Release dates, file
names and provenance came from PyPI's JSON and simple-index APIs. Tags, releases, commits, the
off-peak pull request and the advisories came from the GitHub API. LiteLLM's, TypeSafe's and
DeepSeek's docs were read on their sites. Counts of releases are of distinct versions with at
least one uploaded file.
