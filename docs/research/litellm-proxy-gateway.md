# LiteLLM's proxy as the lab's gateway

Query date: 2026-11-08. Source: LiteLLM **v1.108.3**, the newest stable release (2026-11-07, the
first long-term-support line), read at commit
[`b7d579f`](https://github.com/BerriAI/litellm/tree/b7d579fab2818968322ef96e5b8e98dc8178fafc), and
LiteLLM's docs repository at commit
[`2169b14`](https://github.com/BerriAI/litellm-docs/tree/2169b14a14916717563fd26e1f38fe7b46fe54c6)
(2026-11-08). A docs file `docs/X.md` there is the page `https://docs.litellm.ai/docs/X`; a blog
file `blog/Y/index.md` is `https://docs.litellm.ai/blog/Y`. Both were downloaded as archives and
read only; nothing was installed or run, no key was used, no model was called.

Each claim is marked **Read** (seen in the source or docs at those commits), **Inferred** (follows
from what was read, not tested) or **Not established**. The LiteLLM Python library (the Jev
client, its retries, the cost function, usage fields, the version pin) is
[ticket 2](https://github.com/liuhlab/liulab-llms/issues/2)'s; where the proxy only reuses it, this
note says so and stops.

Terms used below. **Proxy**: LiteLLM's server. **Virtual key**: a key the proxy issues to a caller;
provider keys stay on the server. **Team**, **internal user**, **organization**: the proxy's
groupings of keys. **Tag**: a free-text label on a request. **Deployment**: one provider model
entry in the proxy's `model_list`; a **model group** is the public name callers use, served by one
or more deployments. **Router**: the part that picks a deployment, retries and falls back.
**Pass-through**: a route that forwards the raw request to one provider. **Enterprise**: needs
BerriAI's paid licence (`premium_user` in the source).

## Question

Could LiteLLM's proxy be the lab's gateway, the one long-running service every model call goes
through, and what would we still build?

## Answer

- **People and money fit, mostly free.** Keys per person, budgets per key, person, team and the
  whole proxy, budget windows, and a reservation that holds back budget for requests in flight
  are all open source. Organizations (a level above teams), budgets per model, tags fixed on a
  key, and the `/spend/report` family need the enterprise licence (read).
- **Rate limits are per minute, not per second.** A deployment can carry hard requests-per-minute
  and best-effort tokens-per-minute limits, shared by every key. Calls at once are counted per
  worker process. Nothing reads "80 requests a second" (read). The router retries 429 and every
  5xx, so 503 and 529 too, if the error keeps its status (read; the 529 mapping is ticket 2's).
- **The cache is not the one we need.** Redis, S3 and GCS can keep entries forever. But the key
  names the model group, not the model behind it, three commits changed the key material in
  October 2026, and Jev calls are never cached on either Jev route (read). Cache hits are logged,
  marked and priced at $0 (read).
- **Jev has two routes, each missing something.** `/v1/systemone` goes through the router: retries,
  budgets, key limits, a cost header. `/typesafe/...` returns TypeSafe's response unchanged but
  has no retries and no cost header (read, retries inferred). Neither is cached (read).
- **The spend record is close.** One row per request with tokens, cost, tags, free-form metadata
  and a cache-hit flag, and the cost comes back in a response header (read). Retries are not rows
  of their own, and stored prompts are truncated (read). DeepSeek's off-peak rates are built in
  (read).
- **Running it is real operations work.** PostgreSQL for keys and spend, Redis once there is more
  than one worker, a container image, 1 vCPU and 4 GiB per worker, weekly releases that each list
  breaking changes (read). LiteLLM's releases were compromised twice in 2026; the second time
  (October) the proxy's container images were poisoned too (read).

## People and money

**Keys per person.** Virtual keys can be owned by an internal user and belong to a team; issuing
them through the admin API needs no licence (read: no licence check on key creation except for the
fields below, [`key_management_endpoints.py#L1527-L1532`][s-keytags],
[`_types.py#L4531-L4542`][s-premfields]). Every budget needs PostgreSQL; with no database the
global budget fails open (read, [`users.md#L25`][d-users25]).

**Budgets: what is free and what is not.**

| Level | Licence | Evidence |
| --- | --- | --- |
| Whole proxy (`litellm_settings.max_budget`) | Free | read, [`users.md` Global Proxy][d-users25] |
| Team, team member, internal user, key, with a duration | Free | read: none of the proxy's licence checks covers them |
| Tag budgets (`/tag/new` with `max_budget`) | Free | read, no licence check in [`tag_management_endpoints.py`][s-tagmgmt] |
| Tags fixed on a key | Enterprise | read, [`key_management_endpoints.py#L1531`][s-keytags] |
| Organizations (a level above teams) | Enterprise | read, [`organization_endpoints.py#L99-L110`][s-org] |
| Budget per model on a key, user or team | Enterprise | read, [`key_management_endpoints.py#L8174-L8180`][s-keymmb], [`common_utils.py#L81-L92`][s-teammmb] |
| Team admins, JWT or OAuth2 login | Enterprise | read, [`team_endpoints.py#L2867-L2873`][s-teamadmin], [`user_api_key_auth.py#L2238-L2246`][s-jwt] |

**A person across repos.** If a key belongs to a team, only the team and team-member budgets
apply; the owner's personal budget does not (read, [`users.md#L16`][d-users16]). So with one team
per repo, nothing caps one person's total across all repos, and with one team per person, nothing
caps a repo (inferred). A free way round: personal keys with a user budget, plus a request tag per
repo with a tag budget. A caller can omit a request tag unless `reject_clientside_metadata_tags` is
set, and that setting allows only tags fixed on keys, which are enterprise (inferred from the two
reads above and [`reject_clientside_metadata_tags.md`][d-rejecttags]).

**Requests in flight at a budget.** Budget reservation is on by default. Before a request goes
out, the proxy prices its worst case from `max_tokens` (or the model's limit) and reserves it; if
the reservation would cross the budget the request is refused before the provider sees it; the
real cost replaces the reservation afterwards (read, [`users.md#L914-L950`][d-reserve],
[`budget_reservation.py#L254`][s-reserve]). Routes without token pricing cannot reserve and fall
back to recorded spend (read, same section). The hot-path counter lives in Redis; a stale Redis can
under-count unless `fail_closed_budget_enforcement` is set, which then answers 503 when spend
cannot be checked (read, [`users.md#L952-L965`][d-failclosed]).

## Rate limits

**A limit per provider model, shared by every key.** A deployment's `rpm` and `tpm` steer routing
by default; with `router_settings.optional_pre_call_checks: [enforce_model_rate_limits]` they become
hard limits that answer 429 with `retry-after: 60` (read, [`load_balancing.md#L91`][d-lb91]).
Requests per minute are exact; tokens per minute are checked before a call and counted after it,
so they can overshoot (read, same page). `itpm`/`otpm` split input and output tokens (read,
[`io_token_rate_limits.md`][d-iotpm]).

- **Windows are calendar minutes.** The model limit keys its counters by `%H-%M` with a 60-second
  expiry ([`model_rate_limit_check.py#L49`][s-mrl49], [`#L188`][s-mrl188]); the key and team
  limiter's window is `LITELLM_RATE_LIMIT_WINDOW_SIZE`, default 60 seconds
  ([`parallel_request_limiter_v3.py#L895`][s-prl895]) (read). Jev's 80 requests a second is 4,800
  a minute, and a minute window lets all 4,800 go in the first second (inferred). No per-second
  limit was found (not established that none exists; none in the docs or the two limiters).
- **No limit across deployments.** Each deployment counts alone. Jev's limit covers the whole
  organization across every Jev model, so several Jev deployments would each need a share
  (inferred; no provider-wide limit found in the docs).
- **Calls at once.** A deployment's `max_parallel_requests` (default: its `rpm`) is counted inside
  one worker process, not across workers; a request over it gets an immediate 429, with no queue
  (read, [`routing.md#L1317-L1321`][d-maxpar]). Key and team `max_parallel_requests` are shared
  through Redis (read, [`users.md#L1420`][d-rlfail]).

**Retries.** The router retries 408, 409, 429 and every status of 500 and above
([`utils.py#L7614-L7639`][s-shouldretry], [`router.py#L8552-L8620`][s-routerretry]), with
exponential backoff for rate limits that honours the provider's retry headers
([`router.py#L8647-L8690`][s-sleep]; [`routing.md#L1560`][d-retries]) (read). A single-deployment
model group is not put in cooldown by default ([`cooldown_handlers.py#L533`][s-cooldown]) (read).
The router owns retries and sets the provider SDK's own retries to 0, so a request is not retried
twice (read, [`routing.md#L1617`][d-ownretries]). Whether a Jev 529 reaches the router as an error
with status 529 depends on the SDK's error mapping: ticket 2.

## Cache

**Stores.** Redis (and Valkey and managed Redis), S3, GCS, Azure Blob, in-memory, three semantic
caches, and disk ([`types/caching.py#L10-L19`][s-cachetypes]; [`caching.md#L21`][d-caching21])
(read). The disk cache is still in v1.108.3 ([`disk_cache.py`][s-diskcache]) but its removal
([BerriAI/litellm#48114](https://github.com/BerriAI/litellm/pull/48114), merged 2026-10-30) is on
`main` and in the 1.110.0 dev builds, so it leaves with the next minor line (read: the GitHub
compare API shows the merge commit is not an ancestor of the v1.108.3 tag and is one of
v1.110.0-dev.2).

**Forever.** With no `ttl`, Redis writes with no expiry ([`base_cache.py#L26-L33`][s-getttl],
[`redis_cache.py#L872-L884`][s-redisset], default `default_redis_ttl = None` at
[`__init__.py#L408`][s-redisttl]); S3 writes with no `Expires` ([`s3_cache.py#L66-L95`][s-s3]); GCS
has no expiry code at all ([`gcs_cache.py`][s-gcs]) (read). The in-memory cache keeps 10 minutes
and a bounded number of entries ([`in_memory_cache.py#L34-L46`][s-inmem]) (read). Redis keeps data
forever only if the Redis server is set to persist to disk and never evict; that is Redis's
configuration, not LiteLLM's, and LiteLLM's own sizing page says to avoid eviction
([`redis_sizing.md#L27`][d-redissizing]) (inferred). A caller can still set a shorter `ttl` per
request ([`caching_controls.md#L19`][d-cachectl]) (read).

**What the key covers.** A SHA-256 of every OpenAI-style parameter in the request
([`caching.py#L379-L433`][s-cachekey]) (read). Three things matter here:

- The `model` in the key is the **model group** (the public alias), not the provider model behind
  it ([`caching.py#L449-L475`][s-modelparam]). Swap the deployment behind an alias and old answers
  keep being served (read; effect inferred).
- The key does not include the virtual key or team, so every caller shares entries (read: no such
  field in the key material; semantic caches are the exception).
- The key material changes between releases. Three October 2026 commits changed it: where the
  router's model group is read from (#44542), a `model_id` suffix (#44985) and the
  reasoning-summary setting (#36460) ([commit history of `caching.py`][s-cachehistory]) (read: the
  three diffs). Each touches only some requests, so whether an upgrade turns our existing entries
  into misses is not established; that a rerun after an upgrade can pay again is inferred.

**Is a cache hit logged and marked?** Yes. A hit is priced at $0
([`litellm_logging.py#L1907-L1911`][s-hitcost]), written as its own spend-log row with
`cache_hit = True` and the cache key ([`spend_tracking_utils.py#L823-L841`][s-hitrow];
[`schema.prisma#L739-L740`][s-schema]), and the response carries `x-litellm-cache-key`
([`caching.md#L155`][d-caching155]; [`caching_handler.py#L397-L402`][s-hitkey]) (read).

**What is cached.** Chat and text completions, embeddings, transcription, rerank, the Responses
API and Anthropic Messages, nothing else ([`types/caching.py#L27-L59`][s-calltypes]) (read). Decision
calls (`asystemone`, `adecisions`) are not in the list, so Jev is never cached (read). Pass-through
routes have no cache path (inferred).

## Jev

**Native support, through the router.** v1.108.3 serves `POST /v1/systemone` (TypeSafe's format)
and `/v1/decisions` (OpenAI's) ([`decisions_endpoints/endpoints.py#L180-L222`][s-systemone]). The
request goes through the same processing as chat completions
([`#L114-L177`][s-processdec]) and the router's retry-and-fallback path
([`router.py#L2424-L2427`][s-routersysone]) (read). The docs list cost tracking with the
`x-litellm-response-cost` header, logging, virtual keys, budgets and rate limits as supported, and
streaming as not ([`decisions.md#L57-L63`][d-decfeatures]) (read). Whether each probability comes
back untouched on this route is the SDK's TypeSafe adapter: ticket 2.

**Pass-through.** `/typesafe/{path}` forwards to TypeSafe with the provider key added on the server
([`llm_passthrough_endpoints.py#L640-L680`][s-tspass]) (read). The docs say "The response is
TypeSafe's own, unchanged" ([`pass_through/typesafe.md#L67`][d-tspass67]) (read), so probabilities
are untouched by construction (inferred). Cost is priced from the response's `usage` and logged
([`#L9`][d-tspass9]) (read).

| | `/v1/systemone` | `/typesafe/...` |
| --- | --- | --- |
| Response | the SDK's translation (ticket 2) | TypeSafe's own bytes (read) |
| Cached | no (read) | no (inferred) |
| Spend logged | yes (read) | yes (read) |
| Cost header to the caller | yes (read) | no ([`response_headers.md#L50`][d-resphdr]) (read) |
| Key and team limits | yes (read) | yes: the pass-through runs the pre-call hooks ([`pass_through_endpoints.py#L1268`][s-passhook]) and reported tokens charge the key's token window ([`pass_through_cost_tracking.md#L81-L83`][d-ptcost]) (read) |
| Deployment limit (`enforce_model_rate_limits`) | not established for decision calls | no: no router (inferred) |
| Retries on 529 | the router retries 5xx (read), if the SDK keeps 529 (ticket 2) | none found in the pass-through code (inferred) |

## The spend record

**One row per request.** `LiteLLM_SpendLogs` holds request id, call type, hashed key, spend,
prompt, completion and total tokens, start and end time, model, model group, provider, user, team,
organization, end user, `request_tags`, `metadata`, `cache_hit`, `cache_key`, status and, when
enabled, `messages` and `response` ([`schema.prisma#L720-L763`][s-schema]) (read).

- **Retries are not their own rows.** Spend logs are written from request-level hooks, which fire
  once per client request; per-attempt hooks exist for custom code
  ([`custom_callback.md#L72`][d-perattempt]) (read that the contract is per request; that spend
  logs follow it is inferred). The count of retries comes back in `x-litellm-attempted-retries`
  ([`response_headers.md`][d-resphdr]) (read).
- **Not recorded whole by default.** Request and response bodies are stored only with
  `store_prompts_in_spend_logs: true` ([`ui_logs.md#L20`][d-uilogs]), and stored strings are cut at
  `MAX_STRING_LENGTH_PROMPT_IN_DB`, default 2048 characters ([`constants.py#L583`][s-maxstr])
  (read). Rows are written in batches, every 10 seconds by default
  ([`config_settings.md#L384`][d-batchwrite]) (read); rows still queued when the process dies are
  lost (inferred).

**Labels a caller attaches.** A request can carry tags in the body, in `metadata.tags` or in an
`x-litellm-tags` header ([`request_tags.md#L51-L110`][d-reqtags]); they land in `request_tags`
(read). Free-form `spend_logs_metadata` (for example `{"pass": ..., "unit": ...}`) lands in the
row's metadata ([`cost_tracking.md` spend logs metadata][d-slmeta]) (read). Neither needs a licence
on the request (read: no check on request-level tags or metadata found). Spend by tag is served by
`/spend/tags` and `/global/spend/tags`, which are free; `/global/spend/report` and the
`/key|user|team|organization/spend/report` routes are enterprise
([`spend_management_endpoints.py#L1540`][s-spendrep], [`#L1851-L1864`][s-spendrep2]) (read). With
our own PostgreSQL, a pass's or unit's spend is also one SQL query over `metadata` or
`request_tags` (inferred). One tag per unit would mean about 200,000 tags; how the daily tag
tables cope with that is not established, so a unit belongs in metadata rather than a tag
(inferred).

**Reading the cost back.** Every native-route response carries `x-litellm-response-cost` and its
parts (input, output, cache read and write, reasoning) (read,
[`response_headers.md#L50-L57`][d-resphdr]; [`common_request_processing.py#L1739-L1750`][s-costhdr]).
Pass-through responses do not (read, same table).

**How cost is priced.** The proxy uses the library's cost function and the bundled price map
(ticket 2). DeepSeek's off-peak schedule is in the map: the `deepseek/deepseek-flash` entry carries
`off_peak_pricing` with weekday windows ([`model_prices_and_context_window.json#L62759-L62790`][s-dsmap]),
each request is priced at the time it completes, and Chinese public holidays are not modelled, so
those days slightly over-count ([`providers/deepseek.md#L189-L198`][d-deepseek];
[`off_peak_pricing.md#L52`][d-offpeak]) (read).

## Running it

**What it needs.**

- **PostgreSQL** for virtual keys, budgets and spend; none of those work without it (read,
  [`users.md#L25`][d-users25]).
- **Redis** once there is more than one worker or replica; without it limits, budgets, cache hits
  and cooldowns are counted per worker. One worker on one machine needs none (read,
  [`redis_requirements.md#L53-L66`][d-redisreq]). The response cache still needs Redis, S3 or GCS
  to outlive a restart (inferred from the in-memory cache's 10-minute life above).
- **A container image**, `ghcr.io/berriai/litellm` (with the database toolchain), pinned to a
  version tag ([`deploy.md#L47`][d-deploy]) or, safer, a digest
  ([`docker_image_security.md#L154`][d-digest]) (read).
- **1 vCPU and 4 GiB per worker** as the floor; on a single VM, one worker per vCPU (read,
  [`prod.md#L133`][d-prod133], [`#L169`][d-prod169]).
- **A salt key** that encrypts the provider keys stored in the database and cannot be rotated once
  models are added ([`deploy.md#L45`][d-deploy]) (read).

**The 2026-03-24 compromise (1.82.7, 1.82.8).** LiteLLM's own posts exist; news reports were not
used. The two PyPI packages carried a credential stealer inside the proxy's own `proxy_server.py`
([`security_update_march_2026#L52`][b-mar52]). LiteLLM says the official image
`ghcr.io/berriai/litellm` was not affected because it pins its dependencies
([`#L36`][b-mar36], [`#L88`][b-mar88]) (read). Their townhall post says the stolen CI secrets
included the GHCR and Docker publishing credentials ([`security_townhall_updates#L44`][b-town44])
(read), so "the image was not affected" rests on LiteLLM's audit of image digests, not an outside
one (inferred). Images are cosign-signed from v1.83.0 (read, [`#L713-L745`][b-mar713]).

**A second compromise, 2026-10-21 (1.107.0).** Not on the map yet. The attacker published
`litellm==1.107.0` to PyPI and pointed the container tags `v1.107.0`, `latest`, `main-latest` and
`main-stable` on GHCR and Docker Hub at malicious images for about two and a half hours
([`security_incident_oct_21_2026#L38`][b-oct38], [`#L54`][b-oct54]) (read). The malicious images
carried a valid signature from the then-current cosign key, so signature checks did not catch them
([`#L193`][b-oct193]) (read). LiteLLM told affected users to rotate "every provider key stored in
the LiteLLM proxy DB" ([`#L103`][b-oct103]) (read); a gateway holding all lab keys is exactly that
target (inferred). Enterprise customers on a private registry were not affected and were receiving
an embargoed security fix ahead of public disclosure ([`#L79`][b-oct79]) (read).

**How often it breaks.** Stable releases ship every Tuesday and bump the minor version; the policy
says only a major version breaks compatibility ([`release_cycle.md#L23-L49`][d-release]) (read). In
practice each of the eight stable minor releases from v1.100.0 (2026-09-06) to v1.108.0
(2026-11-04) has a "Breaking Changes" callout, with roughly 4 to 28 items each (read the callouts;
counts measured by counting bold items in the release-note files, approximate). Examples: in
v1.108.0 the proxy refuses to start when a logging callback cannot load, and a deployment's
concurrency cap now rejects instead of queueing ([`v1.108.0#L67`][r-108];
[`routing.md#L1321`][d-maxpar]) (read). A one-month long-term-support pilot started with 1.108,
which pauses the policy of patching the four newest lines ([`lts_pilot.md#L8-L18`][d-lts]) (read).

## What we would still build around the proxy

- **The response cache.** The proxy does not cache Jev, keys on the alias rather than the model,
  changes its key material between releases, and keeps nothing forever without a persistent Redis
  or a bucket. A cache we own, keyed on the provider model and settings we stamp, still sits in
  the library or in front of the proxy (inferred).
- **A per-second limit for Jev.** Per-minute windows cannot hold 80 requests a second; the library
  still needs its own limiter and backoff on 429 and 529 (inferred).
- **The record of every call, whole.** Turn on prompt storage and raise the truncation limit, or
  keep our own transcript; retries need a per-attempt hook or our own log (inferred).
- **Labels.** Pass and unit go in `spend_logs_metadata` on every request, and per-pass reports are
  our SQL, since the report routes are enterprise (inferred).
- **The person-and-repo budget scheme**, given that team keys skip the personal budget (inferred).
- **Operations.** The VM, PostgreSQL with backups, Redis or a bucket for the cache, TLS, the
  container pinned by digest, a weekly read of breaking changes, key rotation (inferred).

## Where the proxy does not fit

- **Library first, no server.** The map's call layer is one process that "needs no server". A
  gateway is a server every lab repo depends on; when it is down, no pass runs (inferred).
- **Reproducible forever.** The proxy's cache is built to save money, not to replay the same
  answer across upgrades and model swaps (inferred from the key's contents and history).
- **Jev.** No cache on either route; the unchanged response comes only on the route without
  retries or a cost header (read and inferred, above).
- **Organization-wide per-second limits**, which LiteLLM does not express (read and inferred,
  above).
- **Key custody.** Putting every provider key in one internet-facing service makes it the target
  that both 2026 compromises went after (inferred).

## Not established

- Whether `enforce_model_rate_limits` applies to `/v1/systemone` deployments.
- Whether budget reservation can price a `/v1/systemone` or `/typesafe/` request before it goes out.
- Whether a Jev 529 reaches the router with status 529, and whether `/v1/systemone` returns each
  probability untouched (ticket 2).
- Whether an upgrade turns existing cache entries for our request shapes into misses.
- How the daily tag spend tables behave with hundreds of thousands of tags.
- Whether spend-log rows still queued at a crash are lost (inferred above, not read).
- An outside audit of LiteLLM's statement that the March images were clean.
- Licence limits on single sign-on beyond a debug route; keys can be issued without it.

[s-keytags]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/management_endpoints/key_management_endpoints.py#L1527-L1532
[s-premfields]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/_types.py#L4531-L4542
[s-tagmgmt]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/management_endpoints/tag_management_endpoints.py
[s-org]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/management_endpoints/organization_endpoints.py#L99-L110
[s-keymmb]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/management_endpoints/key_management_endpoints.py#L8174-L8180
[s-teammmb]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/management_endpoints/common_utils.py#L81-L92
[s-teamadmin]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/management_endpoints/team_endpoints.py#L2867-L2873
[s-jwt]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/auth/user_api_key_auth.py#L2238-L2246
[s-reserve]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/spend_tracking/budget_reservation.py#L254
[s-mrl49]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router_utils/pre_call_checks/model_rate_limit_check.py#L49
[s-mrl188]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router_utils/pre_call_checks/model_rate_limit_check.py#L188
[s-prl895]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/hooks/parallel_request_limiter_v3.py#L895
[s-shouldretry]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/utils.py#L7614-L7639
[s-routerretry]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L8552-L8620
[s-sleep]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L8647-L8690
[s-cooldown]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router_utils/cooldown_handlers.py#L533
[s-cachetypes]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/types/caching.py#L10-L19
[s-calltypes]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/types/caching.py#L27-L59
[s-diskcache]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/caching/disk_cache.py
[s-getttl]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/caching/base_cache.py#L26-L33
[s-redisset]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/caching/redis_cache.py#L872-L884
[s-redisttl]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/__init__.py#L408
[s-s3]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/caching/s3_cache.py#L66-L95
[s-gcs]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/caching/gcs_cache.py
[s-inmem]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/caching/in_memory_cache.py#L34-L46
[s-cachekey]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/caching/caching.py#L379-L433
[s-modelparam]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/caching/caching.py#L449-L475
[s-cachehistory]: https://github.com/BerriAI/litellm/commits/v1.108.3/litellm/caching/caching.py
[s-hitcost]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/litellm_core_utils/litellm_logging.py#L1907-L1911
[s-hitrow]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/spend_tracking/spend_tracking_utils.py#L823-L841
[s-hitkey]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/caching/caching_handler.py#L397-L402
[s-schema]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/schema.prisma#L720-L763
[s-systemone]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/decisions_endpoints/endpoints.py#L180-L222
[s-processdec]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/decisions_endpoints/endpoints.py#L114-L177
[s-routersysone]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/router.py#L2424-L2427
[s-tspass]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/pass_through_endpoints/llm_passthrough_endpoints.py#L640-L680
[s-passhook]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/pass_through_endpoints/pass_through_endpoints.py#L1268
[s-maxstr]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/constants.py#L583
[s-spendrep]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/spend_tracking/spend_management_endpoints.py#L1540
[s-spendrep2]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/spend_tracking/spend_management_endpoints.py#L1851-L1864
[s-costhdr]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/litellm/proxy/common_request_processing.py#L1739-L1750
[s-dsmap]: https://github.com/BerriAI/litellm/blob/b7d579fab2818968322ef96e5b8e98dc8178fafc/model_prices_and_context_window.json#L62759-L62790
[d-users16]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/users.md#L16
[d-users25]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/users.md#L25-L36
[d-reserve]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/users.md#L914-L950
[d-failclosed]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/users.md#L952-L965
[d-rlfail]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/users.md#L1420-L1430
[d-rejecttags]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/reject_clientside_metadata_tags.md
[d-lb91]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/load_balancing.md#L91-L146
[d-iotpm]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/io_token_rate_limits.md
[d-maxpar]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/routing.md#L1317-L1321
[d-retries]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/routing.md#L1560
[d-ownretries]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/routing.md#L1617
[d-caching21]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/caching.md#L21-L42
[d-caching155]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/caching.md#L155
[d-cachectl]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/caching_controls.md#L19
[d-redissizing]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/redis_sizing.md#L27
[d-redisreq]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/redis_requirements.md#L53-L66
[d-decfeatures]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/decisions.md#L57-L63
[d-tspass9]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/pass_through/typesafe.md#L9
[d-tspass67]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/pass_through/typesafe.md#L67
[d-resphdr]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/response_headers.md#L44-L57
[d-ptcost]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/pass_through_cost_tracking.md#L81-L83
[d-perattempt]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/observability/custom_callback.md#L72
[d-uilogs]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/ui_logs.md#L20
[d-batchwrite]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/config_settings.md#L384
[d-reqtags]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/request_tags.md#L51-L110
[d-slmeta]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/cost_tracking.md#L1050-L1100
[d-deepseek]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/providers/deepseek.md#L189-L198
[d-offpeak]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/off_peak_pricing.md#L52
[d-deploy]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/deploy.md#L45-L47
[d-digest]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/docker_image_security.md#L154-L175
[d-prod133]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/prod.md#L133-L147
[d-prod169]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/prod.md#L169
[d-release]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/proxy/release_cycle.md#L23-L49
[d-lts]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/docs/enterprise/lts_pilot.md#L8-L18
[r-108]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/release_notes/v1.108.0/index.md#L67-L90
[b-mar36]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/blog/security_update_march_2026/index.md#L36
[b-mar52]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/blog/security_update_march_2026/index.md#L52-L53
[b-mar88]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/blog/security_update_march_2026/index.md#L85-L88
[b-mar713]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/blog/security_update_march_2026/index.md#L713-L745
[b-town44]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/blog/security_townhall_updates/index.md#L44
[b-oct38]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/blog/security_incident_oct_21_2026/index.md#L36-L40
[b-oct54]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/blog/security_incident_oct_21_2026/index.md#L50-L60
[b-oct79]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/blog/security_incident_oct_21_2026/index.md#L77-L80
[b-oct103]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/blog/security_incident_oct_21_2026/index.md#L103
[b-oct193]: https://github.com/BerriAI/litellm-docs/blob/2169b14a14916717563fd26e1f38fe7b46fe54c6/blog/security_incident_oct_21_2026/index.md#L193
