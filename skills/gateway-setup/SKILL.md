---
name: gateway-setup
description: Set up, upgrade, restore or tear down the lab gateway's VM, or add a lab member's tunnel key. Use for any change to the deployed gateway.
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
- **Provider keys** travel from the admin's environment to the VM's root-only file through
  standard input. Never print one, echo it, or put it on a command line. Check that a key
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
| account `tunnel` | lab members' keys: a forward to port 8000, no shell |

`deploy/gateway/install.sh` installs all of it, as root, and is safe to rerun. Rerunning it is
the upgrade, and it restarts both services. Logs are in the journal:
`journalctl -u labllm-gateway`.

## Provider keys

After first setup and after every rebuild, since a new VM has a new boot disk:

```bash
env | grep -E '^(DEEPSEEK|TYPESAFE|OPENAI|ANTHROPIC)_API_KEY=' |
  ssh llm-gateway 'sudo sh -c "umask 077 && cat > /etc/labllm/keys.env" && sudo systemctl restart labllm-gateway'
```

## Add a lab member's key

The member sends their public key line. Add it with the tunnel-only options:

```bash
echo 'restrict,port-forwarding,permitopen="127.0.0.1:8000" <public key line>' |
  ssh llm-gateway 'sudo tee -a /home/tunnel/.ssh/authorized_keys >/dev/null'
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
2. Through a tunnel, send one tiny chat call twice:

   ```bash
   pixi run python -c 'from labllm import Gateway, Model
   m = Gateway(repo="liulab-llms").meter("deploy check", ceiling_usd=0.01)
   for _ in range(2):
       r = m.chat(Model("deepseek", "deepseek-v4-flash", max_output_tokens=8), user="Say OK.")
       print(r.cached, r.cost_usd)'
   ```

   The second line must read `True 0.0`.

## Restore from the nightly dump

Live once anyone is served. The dump is the sure copy:

```bash
gcloud storage ls gs://<bucket>/
ssh llm-gateway 'sudo systemctl stop labllm-gateway &&
  sudo labllm-db dropdb labllm && sudo labllm-db createdb labllm &&
  gcloud storage cat gs://<bucket>/<dump> | sudo labllm-db pg_restore --no-owner -d labllm &&
  sudo systemctl start labllm-gateway'
```

Then run "Check it works": a call answered before the dump comes back as a hit.

## Upgrade

1. Snapshot the data disk ([gcp.md](gcp.md)).
2. Move `GATEWAY_TAG` in the task file to the new release tag.
3. Rerun the launch. Calls in flight ride through the restart: the library resends them.
4. Run "Check it works".

If the new lock moves PostgreSQL to a new major version, the server refuses the old data:
dump, move `pgdata` aside, rerun the install, restore the dump.
