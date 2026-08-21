#!/bin/sh
# Sandbox overlay entrypoint (docker-compose.sandbox.yml).
#
# Starts as root (compose user: "0:0") only to:
#   1. make a container-private /run/netns mount (iproute2 requires a mountpoint);
#      keep it root-owned so only uid-0 sandboxd can create acbrw*/acpkg* inodes;
#   2. start sandboxd (uid 0) on a Unix socket — the only process that execs
#      runsc / ip;
#   3. wait until the socket exists;
#   4. setpriv-drop the API to USER app with no ambient capabilities;
#   5. remain PID 1 so SIGTERM is forwarded to sandboxd and the API.
# The API then runs as app — not root, not privileged:true, and without
# NET_ADMIN / SYS_ADMIN. Those two caps stay on the container for sandboxd.
set -eu

SOCKET="${SANDBOXD_SOCKET:-/run/agentcore/sandboxd.sock}"
SANDBOXD_PID=""
API_PID=""

_term() {
  if [ -n "$API_PID" ]; then
    kill -TERM "$API_PID" 2>/dev/null || true
  fi
  if [ -n "$SANDBOXD_PID" ]; then
    kill -TERM "$SANDBOXD_PID" 2>/dev/null || true
  fi
  wait || true
  exit 0
}

if [ "$(id -u)" = "0" ]; then
  trap _term TERM INT

  if ! command -v setpriv >/dev/null 2>&1; then
    echo "api-sandbox-entrypoint: setpriv not found (util-linux); cannot drop to app" >&2
    exit 1
  fi
  mkdir -p /run/netns
  if ! grep -q ' /run/netns ' /proc/mounts; then
    mount --bind /run/netns /run/netns
  fi
  chmod 0755 /run/netns
  # setpriv 不改环境：compose user 0:0 会把 HOME 设成 /root。asyncpg 默认
  # ssl=prefer，按 Path.home() 读 ~/.postgresql/postgresql.key；uid app 读
  # 0600 root 私钥 → PermissionError → /readyz 误判库挂。
  # 私钥必须 app 可读且不过宽（0600；0644 会被 OpenSSL/libpq 拒绝）。
  mkdir -p /home/app
  if [ -d /root/.postgresql ]; then
    mkdir -p /home/app/.postgresql
    cp -a /root/.postgresql/. /home/app/.postgresql/
    chown -R app:app /home/app/.postgresql
    chmod 0700 /home/app/.postgresql
    find /home/app/.postgresql -type f -name '*.key' -exec chmod 0600 {} +
    find /home/app/.postgresql -type f ! -name '*.key' -exec chmod 0644 {} +
  fi
  chown app:app /home/app
  chmod 0700 /home/app
  # 即使 HOME 仍漏成 /root，uid app 也进不去（官方镜像 /root 常是 0755）。
  chmod 0700 /root 2>/dev/null || true
  export HOME=/home/app
  export USER=app
  export LOGNAME=app

  mkdir -p "$(dirname "$SOCKET")"
  python -m agentcore.tools.sandbox.sandboxd &
  SANDBOXD_PID=$!

  i=0
  while [ ! -S "$SOCKET" ]; do
    i=$((i + 1))
    if [ "$i" -gt 100 ]; then
      echo "api-sandbox-entrypoint: sandboxd socket not ready: $SOCKET" >&2
      kill -TERM "$SANDBOXD_PID" 2>/dev/null || true
      exit 1
    fi
    if ! kill -0 "$SANDBOXD_PID" 2>/dev/null; then
      echo "api-sandbox-entrypoint: sandboxd exited before socket was ready" >&2
      exit 1
    fi
    sleep 0.1
  done

  # Drop to app. Do not keep inheritable/ambient caps — sandboxd holds them.
  setpriv \
    --reuid=app \
    --regid=app \
    --init-groups \
    --inh-caps=-all \
    --ambient-caps=-all \
    -- "$@" &
  API_PID=$!

  status=0
  wait "$API_PID" || status=$?
  kill -TERM "$SANDBOXD_PID" 2>/dev/null || true
  wait "$SANDBOXD_PID" 2>/dev/null || true
  exit "$status"
fi

exec "$@"
