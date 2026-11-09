# Run the gateway

The lab runs one gateway for everyone. You can also start one on your own machine, to try a
change before it reaches the lab's. It keeps its answers in PostgreSQL, a database server.

## Start one on your machine

This starts the gateway with a new, empty database. The database is deleted when the gateway
stops.

```bash
pixi run python -m labllm.server --throwaway
```

It prints the address it serves, `http://127.0.0.1:8000` unless you pass `--port`. Stop it
with Ctrl-C.

To call real models, it needs the provider keys. Set them before you start it, under the
names LiteLLM reads, such as `DEEPSEEK_API_KEY` or `OPENAI_API_KEY`. Jev's key goes in
`TYPESAFE_API_KEY`. Without a key, calls to that provider fail.

## Point the library at it

In another terminal, set `LABLLM_GATEWAY` to the address it printed, then call as usual:

```bash
export LABLLM_GATEWAY=http://127.0.0.1:8000
```

## Settings

A settings file is written in TOML. It names the database to keep, and prices for models
missing from the price table that comes with LiteLLM. The gateway refuses a model with no
price.

```toml
database = "dbname=labllm"
peak_margin_minutes = 5

[prices."my-model"]
input_usd_per_million = 0.5
output_usd_per_million = 2.0
```

Some providers, such as DeepSeek, charge less outside their busy hours. A caller can ask for
its calls to go out only at those times. `peak_margin_minutes` is how long before the busy
hours start the gateway stops sending such calls. It is 5 unless you set it.

Pass it with `--settings settings.toml`. With `--throwaway`, the database line is ignored.

## Rate limits

Each provider limits how fast the lab may call it. The gateway keeps every caller under
those limits together. A call over a limit waits its turn, in the order calls arrived. It is
never refused for going too fast.

Enter each limit from the provider's console under `[limits.<provider>]`. A provider that
sets limits for each model gets one table per model, such as
`[limits.<provider>."<model>"]`. A model's own table replaces its provider's. There are
three limits, and you set only the ones the provider has:

- `requests_per_second`
- `input_tokens_per_second`
- `calls_at_once`

If the console gives a limit per minute, divide it by 60. The numbers below are made up, to
show the form. Copy each real one from its provider's console:

```toml
retry_budget_minutes = 5

[limits.some-provider]
requests_per_second = 10
input_tokens_per_second = 20_000

[limits.other-provider]
calls_at_once = 8

[limits.other-provider."some-model"]
requests_per_second = 2.5  # 150 a minute
```

When a provider is busy or cannot be reached, the gateway waits and tries again. It also
holds back that provider's other calls while it waits. `retry_budget_minutes` is how long a
call keeps trying before it comes back unavailable. It is 5 unless you set it. A provider
whose balance or monthly cap is used up stops the call at once.

## Retire a bad answer

The gateway replays an answer forever. To make it ask the provider again, retire the
answer in the database. The row stays, so the record still shows what was paid:

```sql
UPDATE exchanges SET retired_at = now() WHERE key = '<key>' AND retired_at IS NULL;
```
