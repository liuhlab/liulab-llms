---
name: gateway-setup
description: Set up, upgrade, restore or tear down the lab gateway's VM, move its LiteLLM pin, or add a provider key or a lab member's tunnel key. Use for any change to the deployed gateway.
---

# The gateway's VM

The lab gateway, `python -m labllm.server`, runs on one VM and listens on its loopback port
8000. People reach it through an SSH tunnel; the only open port is SSH, key only. The install
is cloud-neutral, in `deploy/gateway/`. Each cloud adds its own section: GCP with SkyPilot,
the current deployment, is [gcp.md](gcp.md).

## Who runs what

- **First setup**, while the gateway serves no one: you run every step. Stop for a person
  only when a `gcloud` login has expired or a provider key is missing from the admin's
  environment, and say exactly which.
- **Once it serves anyone**, every change is a live-service change: show the admin the
  exact commands and let the admin run them. Upgrade, restore, a member's key and teardown
  are live changes then.

## Rules

- **Public repo.** In the repo, a commit or an issue, write placeholders: `<project>`,
  `<bucket>`, `<zone>`, `<region>`, `<instance>`, `<gateway-ip>`. Never a host, an IP
  address, a person's user name, a key path or the project ID.
- **Provider keys** travel from the admin's environment, or a mode-600 file the admin typed
  them into, to the VM's root-only file through standard input. Never print one, echo it, or put it on a command line. Check that a key
  exists by name only:
  `printenv | cut -d= -f1 | grep -E '^(DEEPSEEK|TYPESAFE|OPENAI|ANTHROPIC)_API_KEY$'`.
- `ssh llm-gateway` is the admin's full shell: SkyPilot's SSH entry on the admin's machine.

## What runs on the VM

| Where | What |
| --- | --- |
| `/opt/labllm/liulab-llms` | the repo at the release tag, with `pixi install --locked -e gateway` |
| `/mnt/data/pgdata` | PostgreSQL's data, on the data disk |
| `/etc/labllm/keys.env` | provider keys: root only, on the boot disk, so no backup holds them |
| `/etc/labllm/settings.toml` | copied from `deploy/gateway/settings.toml`: the rate limits |
| `labllm-postgres`, `labllm-gateway` | the two services, enabled at boot |
| `labllm-dump.timer` | the nightly `pg_dump` to the backup bucket |
| `sudo labllm-db <tool>` | runs `psql`, `pg_dump` or `pg_restore` against the gateway's database |
| account `tunnel` | lab members' public keys, in `/mnt/data/tunnel/authorized_keys` so a rebuild keeps them: a forward to port 8000, no shell |

`deploy/gateway/install.sh` installs all of it, as root, and is safe to rerun. Rerunning it is
the upgrade, and it restarts both services. Logs are in the journal:
`journalctl -u labllm-gateway`.

## Provider keys

After first setup and after every rebuild, since a new VM has a new boot disk:

```bash
env | grep -E '^(DEEPSEEK|TYPESAFE|OPENAI|ANTHROPIC)_API_KEY=' |
  ssh llm-gateway 'sudo sh -c "umask 077 && cat > /etc/labllm/keys.env" && sudo systemctl restart labllm-gateway'
```

To add or replace one key and keep the others (OpenAI's here; change all three names for
another). The file is swapped in only if it holds the key:

```bash
grep '^OPENAI_API_KEY=' <key file> | ssh llm-gateway 'sudo sh -c "umask 077 &&
  { grep -v ^OPENAI_API_KEY= /etc/labllm/keys.env; cat; } > /etc/labllm/keys.env.new &&
  grep -q ^OPENAI_API_KEY=. /etc/labllm/keys.env.new &&
  mv /etc/labllm/keys.env.new /etc/labllm/keys.env" && sudo systemctl restart labllm-gateway'
```

Then delete the key file.

## Add a lab member's key

The member sends their public key line. Add it with the tunnel-only options:

```bash
echo 'restrict,port-forwarding,permitopen="127.0.0.1:8000" <public key line>' |
  ssh llm-gateway 'sudo tee -a /mnt/data/tunnel/authorized_keys >/dev/null'
```

The member then opens a tunnel and points the library at it:

```bash
ssh -N -L 8000:127.0.0.1:8000 tunnel@<gateway-ip>
export LABLLM_GATEWAY=http://127.0.0.1:8000
```

Check both halves with the key: the tunnel answers, and `ssh tunnel@<gateway-ip>` prints
`This account is currently not available.` To remove a member, delete their line.

## Check it works

1. `ssh llm-gateway 'systemctl is-active labllm-postgres labllm-gateway labllm-dump.timer'`
   prints `active` three times.
2. Open a tunnel (`ssh -N -L 8000:127.0.0.1:8000 llm-gateway`) and, beside it, send one tiny
   chat call twice. Thinking off keeps the reply from coming back empty, which is retried
   and paid again:

   ```bash
   LABLLM_GATEWAY=http://127.0.0.1:8000 pixi run python -c 'from labllm import Gateway, Model
   m = Gateway(repo="liulab-llms").meter("deploy check", ceiling_usd=0.01)
   model = Model("deepseek", "deepseek-v4-flash", thinking=False, max_output_tokens=16)
   for _ in range(2):
       r = m.chat(model, user="Reply with the single word OK.")
       print(r.cached, r.cost_usd)'
   ```

   The second line must read `True 0.0`.

## Restore from the nightly dump

Live once anyone is served. The dump is the sure copy. Restore it into a new database, compare
it with `sudo labllm-db psql -d labllm_restored`, then swap the names:

```bash
gcloud storage ls gs://<bucket>/
ssh llm-gateway 'sudo labllm-db createdb labllm_restored &&
  gcloud storage cat gs://<bucket>/<dump> | sudo labllm-db pg_restore --no-owner -d labllm_restored'
ssh llm-gateway 'sudo systemctl stop labllm-gateway &&
  sudo labllm-db psql -d postgres -c "ALTER DATABASE labllm RENAME TO labllm_before_restore" \
    -c "ALTER DATABASE labllm_restored RENAME TO labllm" &&
  sudo systemctl start labllm-gateway'
```

Then run "Check it works": a call answered before the dump comes back as a hit. Drop
`labllm_before_restore` once the admin agrees.

## Move the LiteLLM pin

Only when ADR 0006 says it moves: for a model or fix the gateway needs, or an advisory
against the pin.

1. Take the newest final release uploaded at least a week ago and not yanked:
   `https://pypi.org/pypi/litellm/json` lists each one's upload time and yank flag.
2. No advisory names it: OSV answers `{}` to
   `curl -s -X POST https://api.osv.dev/v1/query -d '{"package":{"name":"litellm","ecosystem":"PyPI"},"version":"<version>"}'`.
3. At its tag, re-check the points in
   [the call layer's research resolution](https://github.com/liuhlab/liulab-llms/issues/2)
   and the price-table entries of the providers the gateway uses.
4. In `pyproject.toml`, move the pin and re-check the pins whose comments name the pinned
   LiteLLM. Then `pixi lock`, `pixi run check`, cut a release, and "Upgrade".

## Upgrade

1. Snapshot the data disk ([gcp.md](gcp.md)).
2. Move `GATEWAY_TAG` in the task file to the new release tag.
3. Rerun the launch. It restarts both services; a call sent meanwhile waits and completes,
   since the library resends it for a minute.
4. Run "Check it works".

If the new lock moves PostgreSQL to a new major version, the server refuses the old data:
dump, move `pgdata` aside, rerun the install, restore the dump.
