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

[prices."my-model"]
input_usd_per_million = 0.5
output_usd_per_million = 2.0
```

Pass it with `--settings settings.toml`. With `--throwaway`, the database line is ignored.

## Retire a bad answer

The gateway replays an answer forever. To make it ask the provider again, retire the
answer in the database. The row stays, so the record still shows what was paid:

```sql
UPDATE exchanges SET retired_at = now() WHERE key = '<key>' AND retired_at IS NULL;
```
