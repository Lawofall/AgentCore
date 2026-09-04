#!/bin/sh
# Independent sandboxd (docker-compose.sandbox.yml). PID 1 is uid 0 and is
# the only process that execs runsc / ip. The API container never shares
# this user, these caps, or this entrypoint.
set -eu

SOCKET="${SANDBOXD_SOCKET:-/run/agentcore/sandboxd.sock}"

mkdir -p /run/netns
if ! grep -q ' /run/netns ' /proc/mounts; then
  mount --bind /run/netns /run/netns
fi
chmod 0755 /run/netns

mkdir -p "$(dirname "$SOCKET")"
exec python -m agentcore.tools.sandbox.sandboxd
