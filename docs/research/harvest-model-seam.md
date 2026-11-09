# What harvest's model code does that the call layer must keep

Query date: 2026-11-08. Source: seqforge `main` at commit
[`83cffe2`](https://github.com/liuhlab/seqforge/tree/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61)
(2026-11-06), read in a shallow clone. Nothing was run and no model was called. Every code link
below points at that commit. Each claim is marked **read** (seen in the code or a test at that
commit), **inferred** (follows from what was read, but not tested or run) or **not established**.

Terms: the **call layer** is liulab-llms's lower layer (model calls, response cache, meter, ceiling,
limit on calls at once), which knows nothing of documents, units or passes. The **adapter** is one
of harvest's two provider classes. **Transient** means worth another attempt; a **verdict** is a
failure that another attempt will not fix.

## Question

What does seqforge's harvest do at its model seam that the call layer must keep, so harvest can move
onto it with no change in behaviour? For each behaviour: does it belong in the call layer or stay in
seqforge, and is its word scoped to "one run", which the call layer does not have?

## Answer

- **Read:** the seam is one method. `complete_json(system, user, schema, model, max_tokens)` returns
  the text unvalidated, usage on common keys, and the mode the call was made in
  ([providers.py:109-136][pv109]). A `TokenMeter` wraps an adapter, has the same method, and is the
  only thing that counts, refuses and records ([meter.py:1-21][mt1]).
- **Read:** everything at that line can move: each Exchange kept whole, the Transcript, the token
  Ceiling with its reservation, usage normalization, error classification, empty-body handling.
  The retry loop sits one level up in `extract.py` and should move too (inferred). What knows
  documents stays: prompts, the Plan, batching and its fallback, per-document Ledger rows, the
  Blocker and exit codes.
- **Read:** "one run" means the life of one `TokenMeter`: one `harvest extract` over a dataset, or
  one eval case across all its trials. Ceiling, Transcript and Ledger are scoped to it. In a call
  layer with no run, the natural scope is the meter object the caller opens (inferred).
- **Read:** the Ceiling counts **tokens, not money**, on purpose. The map's "spend ceiling" is
  therefore a decision, not a port.
- **Read:** the limit on calls at once is one semaphore for the whole process (at most 24), not per
  provider, shared by every meter in the process. Ceiling and limit have different scopes.
- **Read, effect inferred:** four gaps worth fixing on the move. Per-document Ledger rows drop the
  spend of failed requests. A failed exchange records no mode. The transcript is not written on an
  `llm_unavailable` exit. A missing API key is counted as an exchange.
- **Read:** harvest has no response cache, no price, no temperature or seed, no `finish_reason`
  and no timestamps. Nothing to keep there.

## How the seam is wired

**Read.** One request travels: `harvest extract` or `eval run` builds a `TokenMeter` around an
adapter → `extract_planned` fans batches out on a thread pool and holds an in-flight slot →
`extract_batch` → `_complete_with_retry` (the retry loop) → `TokenMeter.complete_json` (reserve,
call, bank) → adapter → provider SDK ([cli/harvest.py:305-323][ch305], [plan.py:848-872][pl848],
[extract.py:511-528][ex511], [extract.py:334-370][ex334], [meter.py:304-330][mt304]).

Every caller inside seqforge, found by searching `src/` and `tests/` for each name the three modules
export (read):

| Caller | What it uses |
| --- | --- |
| `cli/harvest.py` (`harvest extract`) | `resolve_provider`, `TokenMeter(ceiling, subject)`, `CeilingExceeded`, `ProviderUnavailable`; writes `usage.json` and `transcript.jsonl` |
| `evals/run.py` (`eval run`) | one `TokenMeter` per case, `record` off without a directory, `n_exchanges` read mid-life as a delta, `write_transcript` |
| `evals/report.py` | `read_transcript`, `Exchange`, `RAW_KEYS` |
| `cli/eval.py` | `resolve_provider`, `ProviderUnavailable` |
| `harvest/extract.py` | `LLMProvider`, `LLMResponse`, `ProviderUnavailable`, `schema_prompt`; owns the retry loop |
| `harvest/plan.py` | `CHARS_PER_TOKEN`, `LLMProvider`; owns the thread pool and the in-flight limit |
| `io/remote.py` | the other direction: the seam imports `_RETRY_STATUS`, `_MAX_RETRIES`, `retry_delay` from here |
| `tests/test_extract.py`, `test_cli.py`, `test_evals.py` | the seam's tests |

## Behaviours at the seam

**Home** says where a behaviour belongs: **call** (call layer), **seqforge**, or **split** (the
cell says which half goes where). **Run word** says whether harvest's own word for it is scoped to
one run.

### Recording: Exchange, Transcript, Ledger

| # | Behaviour | What it does | Home | Run word | Label |
| --- | --- | --- | --- | --- | --- |
| 1 | Each Exchange kept whole | A frozen record of one request: system-prompt sha256, user text, returned text verbatim, usage, mode, model, error ([meter.py:139-183][mt139]; [GLOSSARY.md:64-69][gl64]) | call | no | read |
| 2 | A retry is its own exchange | A failed attempt is banked too, with the provider's message, empty text and whatever usage the failure reported ([meter.py:304-325][mt304]; tests [test_extract.py:774-805][te774], [1077-1099][te1077]) | call | no | read |
| 3 | Two edges of "an exchange" | A failed attempt is banked with an empty mode ([meter.py:321][mt321]). A missing API key raises inside `complete_json` ([providers.py:243-268][pv243]), so it is banked as an exchange though nothing was sent; a test pins that count ([test_extract.py:399-402][te399], [774-793][te774]) | call: decide both | no | read |
| 4 | Response handed back untouched | The meter never reads the text for meaning and returns it byte-identical ([meter.py:18-20][mt18]; test [test_extract.py:807-817][te807]) | call | no | read |
| 5 | The Transcript | A snapshot: provider, prompts keyed by sha256 (normally one), exchanges in order, built under the lock ([meter.py:186-219][mt186], [366-373][mt366]). `record=False` keeps counts but no text ([meter.py:280-296][mt280], [449-451][mt449]); evals turns it off with no directory ([evals/run.py:286-299][er286]) | call | yes ([GLOSSARY.md:91-95][gl91]) | read |
| 6 | The Transcript on disk | JSON lines: a header (provider, prompts, count), then one line per exchange; written to a temp file and renamed; a reader for the round trip ([transcript.py:26-76][tx26]). Written at the end and on a ceiling breach ([cli/harvest.py:324-336][ch324], [379][ch379]), but neither it nor the Ledger is written on an `llm_unavailable` exit ([cli/harvest.py:342-343][ch342]) | split: format can move; the folder stays | yes | read |
| 7 | Exchange joined to its document | The exchange keeps only the user text, so seqforge parses the first `Document sha256:` line back out of it ([extract.py:251-268][ex251]; [evals/report.py:263-268][rp263]). A batch is booked to its first document | seqforge; a caller tag on each request would replace the parse | no | read; tag inferred |
| 8 | The Ledger | `usage.json`: provider, model, prompt version, totals (meter usage, `n_calls` from the meter, `n_documents`) and one row per document (scope, subject, sha, model, mode, usage) ([cli/harvest.py:345-363][ch345], [547-581][ch547]) | split: totals call; rows seqforge | yes ([GLOSSARY.md:97-100][gl97]) | read |
| 9 | Rows miss failed spend | A request whose answer is not used leaves its usage only in the meter: the retry loop raises without the summed usage ([extract.py:362-365][ex362]); a failed batch's outcome is discarded ([plan.py:861-865][pl861]); an unanswered document carries none ([plan.py:841-846][pl841]). An eval case's `usage` sums outcomes ([evals/run.py:707-716][er707]) while its `n_calls` comes from the meter ([evals/run.py:740][er740]) | call: book cost per request by tag | — | read; size not established |
| 10 | Glossary wording | The glossary says the meter "is what writes" a Ledger ([GLOSSARY.md:100][gl97]); the meter writes no file ([meter.py:15-16][mt1]) and the CLI writes `usage.json` | — | — | read |

### Usage

| # | Behaviour | What it does | Home | Run word | Label |
| --- | --- | --- | --- | --- | --- |
| 11 | Usage on common keys | `input_tokens` is every billed input token: Anthropic's three buckets summed, OpenAI's `prompt_tokens` as is. `cache_read_tokens` and `cache_write_tokens` are a breakdown of it, not extra; DeepSeek's `prompt_cache_hit_tokens` maps to `cache_read_tokens`. No reasoning-token key ([providers.py:364-403][pv364]) | call | no | read |

### Spending: Ceiling and Plan

| # | Behaviour | What it does | Home | Run word | Label |
| --- | --- | --- | --- | --- | --- |
| 12 | Ceiling counts raw tokens | Input plus output, cached and cache-write input included: "a backstop and not a price" ([meter.py:34-58][mt34]; test [test_extract.py:981-994][te981]). No price appears anywhere in harvest (searched) | call | yes ([GLOSSARY.md:71-75][gl71]; "in this run", [cli/harvest.py:153-159][ch153]) | read |
| 13 | Admission by reservation | Before a request, deduct an estimate: the larger of prompt characters ÷ 4 and the mean real cost of exchanges banked so far. Admit only if spent + reserved + estimate ≤ ceiling; otherwise refuse, un-issued. Release the estimate on every exit path, after banking. Requests in flight finish and are banked. One lock throughout. `0` or `None` means no ceiling ([meter.py:231-267][mt231], [283-284][mt280], [376-421][mt376]; tests [test_extract.py:852-1050][te852]) | call | yes | read |
| 14 | Refusal is its own error | `CeilingExceeded` is not a `ProviderUnavailable`, so the retry loop does not back off on it and partial mode does not swallow it ([meter.py:71-108][mt71]; [plan.py:822-827][pl822]; test [test_extract.py:1508-1523][te1508]). `.blocker()` builds seqforge's Blocker with a `--ceiling` remedy, importing seqforge's model ([meter.py:31][mt31], [110-136][mt110]) | split: exception and numbers call; Blocker, exit 3, remedy seqforge | — | read |
| 15 | What "one run" is in code | One `TokenMeter` per `harvest extract`, its subject the record set's query ([cli/harvest.py:277-290][ch277], [319][ch319]); one per eval case across every trial, "because a case is a dataset" ([evals/run.py:241-243][er241], [286-299][er286]) | — | yes | read |
| 16 | The Plan | The exact documents, batches, request count and input-token estimate ((requests × system-prompt chars + document chars) ÷ 4), computed before a provider is resolved; `--dry-run` prints it ([plan.py:262-418][pl262]; [cli/harvest.py:233-234][ch233], [295-301][ch295]). It uses the meter's `CHARS_PER_TOKEN` so plan and admission agree ([meter.py:46-53][mt46]; [plan.py:381-387][pl381]) | seqforge; the call layer exposes the estimator it admits by, usable with no credential | "one extraction" ([GLOSSARY.md:77-82][gl77]) | read; estimator need inferred |
| 17 | `max_tokens` is the caller's | Sized per batch from its ask and width, floor 8,000, cap 32,000 ([plan.py:513-537][pl513]; [extract.py:154][ex154]). Both DeepSeek models were measured honouring it to the token, with no silent clamp ([plan.py:134-139][pl130]) | call: pass through unchanged | no | read |

### Errors and retries

| # | Behaviour | What it does | Home | Run word | Label |
| --- | --- | --- | --- | --- | --- |
| 18 | Retry or verdict | Duck-typed on `status_code` and `Retry-After`, so neither SDK is imported. Transient: 429, 500, 502, 503, 504, a set borrowed from `io/remote.py`; with no status, a type name containing timeout, connection, remotedisconnected or protocolerror ([providers.py:27][pv27], [56-60][pv56], [92-106][pv92]; [io/remote.py:116-119][rm116]). Tests pin 429 and 503 as transient, 400, 401, 404 as verdicts ([test_extract.py:444-473][te444]). 529 is missing (drift 1) | call, with its own status set | no | read |
| 19 | The error carries the decision | `ProviderUnavailable` carries `transient`, `retry_after` and `usage`, because only the adapter holds the SDK error ([providers.py:63-89][pv63]). Verdicts: SDK missing, no API key ([providers.py:155-158][pv155], [249-257][pv243]); an unknown model is a 400 ([providers.py:39-43][pv29]) | call | no | read |
| 20 | Empty body | DeepSeek's empty JSON-mode body and an Anthropic turn with no text block are raised transient, `retry_after="0"` (retry at once), usage kept ([providers.py:187-200][pv187], [285-301][pv285]; tests [test_extract.py:630-719][te630]) | call | no | read |
| 21 | The retry loop | In `extract.py`, outside the meter: at most 3 retries; wait = integer `Retry-After` capped at 16 s, else 1, 2, 4, 8 s capped at 16; only what is marked transient; usage summed on success ([extract.py:334-370][ex334]; [io/remote.py:120-134][rm116]; tests [test_extract.py:384-441][te384]). Each attempt passes through the meter, so each is admitted against the Ceiling and banked separately. The sleep holds an in-flight slot ([plan.py:848-865][pl848]) | call; harvest's loop must then go, or attempts multiply to 4 × 4 | no | read; consequences inferred |

### Calls at once

| # | Behaviour | What it does | Home | Run word | Label |
| --- | --- | --- | --- | --- | --- |
| 22 | One process-wide limit | `MAX_IN_FLIGHT = min(24, 2 × CPU count)`, one semaphore shared by every pool in the process, not per provider ([plan.py:105-111][pl105]). A batch holds one slot through its fallback re-asks, in sequence, to avoid deadlock ([plan.py:806-812][pl806], [848-872][pl848]). Eval cases run at once on their own pool ([evals/run.py:463-513][er463]), each with its own meter and Ceiling ([evals/run.py:286-299][er286]), under this one limit | call, as a per-provider limit with backoff; the fallback stays seqforge | no | read; scope mismatch inferred |

### Request shape: mode, structured output, caching

| # | Behaviour | What it does | Home | Run word | Label |
| --- | --- | --- | --- | --- | --- |
| 23 | Mode recorded | `{thinking, max_tokens, response_format}`: Anthropic `adaptive` and `json_schema`, OpenAI-shaped `model-default` and `json_object` ([providers.py:109-118][pv109], [201-209][pv187], [302-313][pv285]). Carried to the outcome and Ledger rows ([extract.py:179-180][ex179], [571][ex559]; test [test_extract.py:325-354][te325]). DeepSeek's record is wrong (drift 2) | call: record what was sent | no | read |
| 24 | Structured output | Anthropic gets a strict JSON schema ([providers.py:179][pv170]); OpenAI-shaped endpoints get JSON mode only and the schema is not sent ([providers.py:263-279][pv260]). The schema also rides in the system prompt for every provider, one prompt and one prompt version ([extract.py:246-248][ex222]; [providers.py:406-431][pv406]; [ADR-0009][adr9]). Seqforge's Pydantic gate decides ([extract.py:402-459][ex402]) | split: send the schema the strictest way each provider allows, return text; `schema_prompt` (seqforge's example) and validation stay | no | read |
| 25 | Prompt caching | Anthropic's system block is marked `cache_control: ephemeral` ([providers.py:175-177][pv170]); DeepSeek caches prefixes itself ([providers.py:213-219][pv212]); keeping the prefix byte-identical is the caller's job ([extract.py:222-233][ex222]) | split: breakpoint call; stable prefix seqforge | no | read |
| 26 | Thinking and sampling | Anthropic sends adaptive thinking ([providers.py:180][pv170]); DeepSeek sends nothing (drift 2). Temperature, seed and `top_p` are never sent (searched) | call | no | read |

### Identity and selection

| # | Behaviour | What it does | Home | Run word | Label |
| --- | --- | --- | --- | --- | --- |
| 27 | Identity proxied | The meter copies the adapter's name, delegates `default_model()`, and passes other attributes through ([meter.py:225-229][mt225], [297-302][mt280], [332-341][mt332]). Provenance is `provider/model` ([extract.py:559-564][ex559]; test [test_extract.py:757-771][te757]) | split: who answered call; stamp format seqforge | no | read |
| 28 | Provider selection | Explicit name, then `SEQFORGE_LLM_PROVIDER`, then whichever credential is present (DeepSeek first); otherwise refuse and list the options ([providers.py:327-361][pv327]; tests [test_extract.py:476-521][te476]). Refusing rather than guessing is a recorded decision ([ADR-0009][adr9]) | split: refuse-not-guess call; variable names and flags seqforge | no | read |
| 29 | Model names | Defaults `claude-opus-4-8` and `deepseek-v4-pro`; a catalogue, not an allowlist, so any name passes through ([providers.py:29-54][pv29]; test [test_extract.py:535-542][te535]). Flash name is legacy (drift 3) | call passes through; default model seqforge | no | read |

### Stays in seqforge, and what is absent

| # | Behaviour | What it does | Home | Label |
| --- | --- | --- | --- | --- |
| 30 | Document policies | Prompts and `EXTRACT_PROMPT_VERSION` ([extract.py:94][ex94]); batching same-ask documents and the per-document fallback ([plan.py:724-879][pl724]); partial versus fail-closed ([plan.py:814-820][pl806]); cost booked to a batch's first document ([extract.py:572-578][ex559]); code overwriting the echoed sha ([extract.py:620-637][ex620]); Blocker and exit codes | seqforge | read |
| 31 | Absent | No response cache, price, temperature or seed, `finish_reason` (truncation shows up only as a JSON shape failure, [extract.py:484-489][ex484]), reasoning-token count, timestamp or latency, and no provider per exchange, only per transcript ([meter.py:200][mt186]) | nothing to keep; the cache is new | read (searched `harvest/`) |

## Words scoped to "one run"

| Word | Harvest's scope | In code | In the call layer |
| --- | --- | --- | --- |
| Ceiling | "one run" | one `TokenMeter` | the meter the caller opens (recommended) |
| Transcript | "one run" | the same meter's record | the same meter |
| Ledger | "one run", by document | totals from the meter, rows from outcomes | totals per meter; rows by caller tag |
| Plan | "one extraction" | `ExtractionPlan` | stays in seqforge, needs no scope |
| Exchange | one request | `Exchange` | no change |

**Read:** "run" already means a sequencing run in seqforge ([resolve/GLOSSARY.md:34-40][glrun]),
and the repo map describes harvest as "what one run spent reading it"
([GLOSSARY-MAP.md:22-23][gmap]). The liulab-llms map avoids "run" for a pass.

**Inferred (a recommendation):** define Ceiling, Transcript and Ledger against the object the
caller opens and holds, the meter. The engine opens one per pass; harvest opens one per extraction,
or per eval case. That keeps harvest's behaviour exactly, because its scope is already the meter's
lifetime, not anything about runs. Naming that object is a glossary decision.

## The interface harvest would need

All inferred from the table above.

1. **One synchronous, thread-safe call.** System, user, schema, model, `max_tokens` and provider
   settings in; unvalidated text, usage on common keys, and the mode as sent out. Harvest calls
   from a thread pool.
2. **Three kinds of failure.** Unavailable after the call layer's own bounded retries, carrying the
   summed usage; a verdict (4xx, no key, no SDK); a ceiling refusal, its own type, carrying ceiling,
   spent, reserved, estimate and exchange count. Harvest's batch fallback needs the first and must
   never catch the third.
3. **A meter the caller opens.** Counters readable at any time (evals reads deltas), usage by key,
   and a Ceiling with reserve-before-issue, where `0` or `None` means none.
4. **The estimator the meter admits by**, importable with no credential, so a dry run prices
   exactly what admission will check.
5. **A transcript.** Prompts once by sha256, every exchange whole, failures included, recording
   switchable off, and a file writer and reader (or harvest keeps `transcript.py`).
6. **A caller tag on each request**, kept on its exchange. It replaces parsing `Document sha256:`
   out of the prompt and lets cost be booked per document or unit, failed attempts included.
7. **Who answered**: provider name and model, for the `provider/model` stamp; refuse rather than
   guess when no provider is named or found.
8. **A limit on calls at once per provider, shared across meters** in one process.
9. **Provider settings passed through and recorded**: DeepSeek's thinking setting sent explicitly,
   Anthropic's prompt-cache breakpoint on the system block.

## The three drifts on seqforge 545, where they touch the seam

1. **529 not retried.** The adapter borrows `io/remote.py`'s status set
   ([providers.py:27][pv27]; [io/remote.py:116-119][rm116]), so a 529, the overload code of both
   Claude and Jev, is a verdict. In the call layer the status set should be its own, with 529;
   `io/remote.py` keeps the network one (inferred). The classification test has no 529 row
   ([test_extract.py:447-453][te444]).
2. **DeepSeek thinking.** The adapter sends no thinking setting and records `model-default` under a
   comment that the API takes no toggle ([providers.py:302-313][pv285]). The same claim sits in
   `plan.py`'s reasoning-headroom comment ([plan.py:179-184][pl165]), whose measured headroom was
   therefore taken with thinking on (inferred from 545's statement that every DeepSeek run so far
   ran at `high`). Fixing it changes the request and the recorded mode, so it changes any cache key
   built from the request (inferred).
3. **Legacy Flash name.** `DEEPSEEK_FLASH_MODEL = "deepseek-v4-flash"` ([providers.py:36-44][pv29]).
   The CLI help names it ([cli/harvest.py:150-151][ch153]), and a test asserts every catalogue
   name starts with `deepseek-v4` ([test_extract.py:527][te523]), which the rename will break.

## Not established

- Whether LiteLLM's exceptions carry `status_code` and `Retry-After` where this classifier reads
  them, and whether its usage object exposes cache reads and writes under names the normalizer can
  map. That is the LiteLLM research ticket's question.
- How a response replayed from the cache should count: as an exchange, against the Ceiling, in the
  transcript. Harvest defines an exchange by tokens spent, and a replay spends none.
- Whether the `provider/model` stamp changes under LiteLLM's model naming (for example a
  `deepseek/` prefix), which would restamp every assertion.
- Whether DeepSeek's reasoning tokens are inside `completion_tokens`, which decides what the raw
  count includes.
- What the Anthropic SDK does with no API key at construction.
- How large the Ledger-row shortfall (row 9) is in practice; no run was measured.
- The prototype helper `common.py` named on the map was not read for this note.

## Sources

seqforge at commit `83cffe21e9a063c55f0ca55ea7a282ea6eca1c61`; issues
[liuhlab/seqforge#539](https://github.com/liuhlab/seqforge/issues/539) and
[liuhlab/seqforge#545](https://github.com/liuhlab/seqforge/issues/545), and the map,
[liuhlab/liulab-llms#1](https://github.com/liuhlab/liulab-llms/issues/1), all read 2026-11-08.

[pv27]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L27
[pv29]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L29-L54
[pv56]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L56-L60
[pv63]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L63-L89
[pv92]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L92-L106
[pv109]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L109-L136
[pv155]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L155-L158
[pv170]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L170-L186
[pv187]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L187-L209
[pv212]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L212-L219
[pv243]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L243-L268
[pv260]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L260-L284
[pv285]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L285-L313
[pv327]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L327-L361
[pv364]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L364-L403
[pv406]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/providers.py#L406-L431
[mt1]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L1-L21
[mt18]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L18-L20
[mt31]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L31
[mt34]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L34-L58
[mt46]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L46-L53
[mt71]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L71-L108
[mt110]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L110-L136
[mt139]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L139-L183
[mt186]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L186-L219
[mt225]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L225-L229
[mt231]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L231-L267
[mt280]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L274-L302
[mt304]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L304-L330
[mt321]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L321
[mt332]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L332-L341
[mt366]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L366-L373
[mt376]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L376-L421
[mt449]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/meter.py#L449-L451
[tx26]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/transcript.py#L26-L76
[ex94]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L94
[ex154]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L154
[ex179]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L179-L180
[ex222]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L222-L248
[ex251]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L251-L268
[ex334]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L334-L370
[ex362]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L362-L365
[ex402]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L402-L459
[ex484]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L484-L489
[ex511]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L511-L528
[ex559]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L559-L582
[ex620]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/extract.py#L620-L637
[pl105]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L105-L111
[pl130]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L130-L163
[pl165]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L165-L187
[pl262]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L262-L418
[pl381]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L381-L387
[pl513]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L513-L537
[pl724]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L724-L879
[pl806]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L806-L820
[pl822]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L822-L827
[pl841]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L841-L846
[pl848]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L848-L872
[pl861]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/plan.py#L861-L865
[ch153]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L144-L159
[ch233]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L233-L234
[ch277]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L277-L290
[ch295]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L295-L301
[ch305]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L305-L323
[ch319]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L319
[ch324]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L324-L336
[ch342]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L342-L343
[ch345]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L345-L363
[ch379]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L379
[ch547]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/cli/harvest.py#L547-L581
[er241]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/evals/run.py#L241-L243
[er286]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/evals/run.py#L286-L299
[er463]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/evals/run.py#L463-L513
[er707]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/evals/run.py#L707-L716
[er740]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/evals/run.py#L740
[rp263]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/evals/report.py#L263-L268
[rm116]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/io/remote.py#L116-L134
[gl64]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/GLOSSARY.md#L64-L69
[gl71]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/GLOSSARY.md#L71-L75
[gl77]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/GLOSSARY.md#L77-L82
[gl91]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/GLOSSARY.md#L91-L95
[gl97]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/GLOSSARY.md#L97-L100
[glrun]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/resolve/GLOSSARY.md#L34-L40
[gmap]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/GLOSSARY-MAP.md#L22-L23
[adr9]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/src/seqforge/harvest/docs/adr/0009-llm-provider-is-pluggable.md
[te325]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L325-L354
[te384]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L384-L441
[te399]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L399-L402
[te444]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L444-L473
[te476]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L476-L521
[te523]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L523-L532
[te535]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L535-L542
[te630]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L630-L719
[te757]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L757-L771
[te774]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L774-L805
[te807]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L807-L817
[te852]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L852-L1050
[te981]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L981-L994
[te1077]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L1077-L1099
[te1508]: https://github.com/liuhlab/seqforge/blob/83cffe21e9a063c55f0ca55ea7a282ea6eca1c61/tests/test_extract.py#L1508-L1523
