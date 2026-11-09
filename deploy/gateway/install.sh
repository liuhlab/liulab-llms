#!/usr/bin/env bash
# Install the lab gateway on an Ubuntu VM, or upgrade it: run as root from this folder.
#
#   sudo GATEWAY_TAG=vYYYY.M.PATCH BACKUP_BUCKET=gs://<bucket> bash install.sh
#
# Rerunning it is the upgrade, and it restarts both services. The data disk must already be
# mounted at /mnt/data. Provider keys are never handled here: they go in /etc/labllm/keys.env.
set -euo pipefail

: "${GATEWAY_TAG:?set GATEWAY_TAG to the release tag the gateway runs}"
BACKUP_BUCKET=${BACKUP_BUCKET:-}
REPO_URL=${REPO_URL:-https://github.com/liuhlab/liulab-llms.git}
PIXI_VERSION=v0.84.1
PIXI_SHA256=c81a16595e9e925fac3b12fe50cedbf415619a421ad2b72c1bba2a6ba6c2454a

DATA=/mnt/data
APP=/opt/labllm
CODE=$APP/liulab-llms
ENVBIN=$CODE/.pixi/envs/gateway/bin
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

as_app() { runuser -u labllm -- env HOME="$APP" PATH="$APP/.pixi/bin:/usr/bin:/bin" "$@"; }

echo "== swap"
if [[ ! -f /swapfile ]]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
fi
swapon --show=NAME --noheadings | grep -qx /swapfile || swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab

echo "== accounts"
# labllm runs the gateway and its database; tunnel holds lab members' keys, tunnel only.
id labllm &>/dev/null || useradd --system --create-home --home-dir "$APP" --shell /usr/sbin/nologin labllm
id tunnel &>/dev/null || useradd --create-home --shell /usr/sbin/nologin tunnel

echo "== code at $GATEWAY_TAG"
if [[ "$(as_app pixi --version 2>/dev/null)" != "pixi ${PIXI_VERSION#v}" ]]; then
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/pixi.tar.gz" \
        "https://github.com/prefix-dev/pixi/releases/download/$PIXI_VERSION/pixi-x86_64-unknown-linux-musl.tar.gz"
    echo "$PIXI_SHA256  $tmp/pixi.tar.gz" | sha256sum --check --quiet
    install -d -o labllm -g labllm "$APP/.pixi" "$APP/.pixi/bin"
    tar -xzf "$tmp/pixi.tar.gz" -C "$APP/.pixi/bin" pixi
    chown labllm:labllm "$APP/.pixi/bin/pixi"
    rm -rf "$tmp"
fi
[[ -d $CODE/.git ]] || as_app git clone --quiet "$REPO_URL" "$CODE"
as_app git -C "$CODE" fetch --quiet --tags --force origin
as_app git -C "$CODE" -c advice.detachedHead=false checkout --quiet --detach "refs/tags/$GATEWAY_TAG"
as_app bash -c "cd '$CODE' && pixi install --locked -e gateway"

echo "== database on $DATA"
mountpoint -q "$DATA" || { echo "no disk is mounted at $DATA" >&2; exit 1; }
if [[ ! -f $DATA/pgdata/PG_VERSION ]]; then
    install -d -m 700 -o labllm -g labllm "$DATA/pgdata"
    as_app "$ENVBIN/initdb" -D "$DATA/pgdata" -U labllm --auth-local=peer \
        --auth-host=scram-sha-256 --encoding=UTF8 --no-locale
fi

echo "== tunnel keys on $DATA"
install -d -m 755 -o root -g root "$DATA/tunnel"
[[ -f $DATA/tunnel/authorized_keys ]] ||
    install -m 600 -o tunnel -g tunnel /dev/null "$DATA/tunnel/authorized_keys"
chown tunnel:tunnel "$DATA/tunnel/authorized_keys"
install -m 644 "$here/sshd-tunnel.conf" /etc/ssh/sshd_config.d/40-labllm-tunnel.conf
if ! sshd -t; then
    rm -f /etc/ssh/sshd_config.d/40-labllm-tunnel.conf
    echo "sshd rejected the tunnel settings; they were removed" >&2
    exit 1
fi
systemctl reload ssh

echo "== services"
install -d -m 755 /etc/labllm
install -m 644 "$here/settings.toml" /etc/labllm/settings.toml
install -m 755 "$here/labllm-db" "$here/labllm-dump" /usr/local/bin/
install -m 644 "$here"/labllm-*.service "$here"/labllm-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet labllm-postgres labllm-gateway
systemctl restart labllm-postgres
timeout 60 bash -c "until '$ENVBIN/pg_isready' -q -h /run/labllm-postgres; do sleep 1; done"
if [[ "$(labllm-db psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'labllm'")" != 1 ]]; then
    labllm-db createdb labllm
fi
if [[ -f /etc/labllm/keys.env ]]; then
    systemctl restart labllm-gateway
else
    echo "no provider keys yet: write /etc/labllm/keys.env, then: systemctl start labllm-gateway"
fi

echo "== nightly dump"
if [[ -n $BACKUP_BUCKET ]]; then
    echo "BACKUP_BUCKET=$BACKUP_BUCKET" >/etc/labllm/backup.env
    systemctl enable --quiet --now labllm-dump.timer
else
    systemctl disable --quiet --now labllm-dump.timer || true
    echo "BACKUP_BUCKET is unset: no nightly dump"
fi
echo "== done: $GATEWAY_TAG"
