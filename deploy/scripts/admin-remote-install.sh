#!/usr/bin/env bash
# Remote install for admin console static files + nginx + CORS + cloudflared ingress.
# Invoked via: ssh user@host 'bash -s' < deploy/scripts/admin-remote-install.sh
# Override hosts via env: ORIGIN / OFFICE_HOST（默认 office.example.com 占位）。
set -euo pipefail

ORIGIN="${ORIGIN:-https://office.example.com}"
OFFICE_HOST="${OFFICE_HOST:-office.example.com}"
ADMIN_ROOT="/opt/agentcore/admin"
DEPLOY="${AGENTCORE_DEPLOY_DIR:-/opt/agentcore/repo/deploy}"
ENVF="$DEPLOY/config/production.env"
NGINX_AVAIL="/etc/nginx/sites-available/office-admin"
NGINX_ENABLED="/etc/nginx/sites-enabled/office-admin"

mkdir -p "$ADMIN_ROOT"
rm -rf "$ADMIN_ROOT/dist"
tar xzf /tmp/admin-dist.tgz -C "$ADMIN_ROOT"
rm -f /tmp/admin-dist.tgz
echo "admin static → $ADMIN_ROOT/dist ($(find "$ADMIN_ROOT/dist" -type f | wc -l) files)"

sudo install -D -m 644 /tmp/office-admin.conf "$NGINX_AVAIL" 2>/dev/null \
  || sudo install -D -m 644 /tmp/deploy/nginx/office-admin.conf "$NGINX_AVAIL"
rm -f /tmp/office-admin.conf /tmp/deploy/nginx/office-admin.conf
ln -sfn "$NGINX_AVAIL" "$NGINX_ENABLED"
sudo nginx -t
sudo systemctl reload nginx
LOCAL_CODE="$(curl -sS -o /dev/null -w '%{http_code}' -H "Host: ${OFFICE_HOST}" http://127.0.0.1:8090/ || true)"
echo "nginx reloaded; local probe :8090 → HTTP ${LOCAL_CODE}"

# admin 现在打自己域的 /api（见 office-admin.conf 顶部：避免与 web 客户端共用 cookie），
# 反代不通 = 控制台整站不可用，所以这里硬校验而非只打印。
API_CODE="$(curl -sS -o /dev/null -w '%{http_code}' -H "Host: ${OFFICE_HOST}" http://127.0.0.1:8090/api/version || true)"
echo "local probe :8090/api/version → HTTP ${API_CODE}"
if [[ "$API_CODE" != "200" ]]; then
  echo "ERROR: admin 的 /api/ 反代不通（HTTP ${API_CODE}）——控制台会整站登不进去。"
  echo "  查：后端是否在 127.0.0.1:8000；office-admin.conf 的 location /api/ 是否装上。"
  exit 1
fi

# admin 走自有 /api 后已是同源，这条 CORS 条目对它不再是必需品；保留是为了回滚安全
# （把 API 指回产品域时立刻可用），且 allowlist 里多一个自家 origin 不构成风险。
[[ -f "$ENVF" ]] || { echo "ERROR: $ENVF missing"; exit 1; }
if grep -qF "$OFFICE_HOST" "$ENVF"; then
  echo "CORS already includes $ORIGIN"
elif grep -q '^CORS_ALLOW_ORIGINS=' "$ENVF"; then
  sed -i "s|^CORS_ALLOW_ORIGINS=\(.*\)|CORS_ALLOW_ORIGINS=\1,${ORIGIN}|" "$ENVF"
  echo "appended $ORIGIN to CORS_ALLOW_ORIGINS"
else
  echo "CORS_ALLOW_ORIGINS=${ORIGIN}" >>"$ENVF"
fi
grep '^CORS_ALLOW_ORIGINS=' "$ENVF"

COMPOSE=( docker compose -p agentcore \
  -f "$DEPLOY/docker-compose.server.yml" \
  -f "$DEPLOY/docker-compose.app.yml" \
  --env-file "$ENVF" )
# 与 finish-server.sh 同口径：默认叠 sandbox；仅 GVISOR_ENABLED=false 时跳过。
_gvisor_off=0
if grep -Eq '^[[:space:]]*GVISOR_ENABLED[[:space:]]*=[[:space:]]*(false|0|no|False|FALSE)[[:space:]]*$' "$ENVF"; then
  _gvisor_off=1
fi
if [[ "$_gvisor_off" -eq 0 ]]; then
  _sandbox_yml=""
  for _cand in \
    "$DEPLOY/docker-compose.sandbox.yml" \
    "${AGENTCORE_HOME:-/opt/agentcore}/repo/deploy/docker-compose.sandbox.yml"; do
    if [[ -f "$_cand" ]]; then
      _sandbox_yml="$_cand"
      break
    fi
  done
  if [[ -z "$_sandbox_yml" ]]; then
    echo "ERROR: 云执行默认开但找不到 docker-compose.sandbox.yml（或设 GVISOR_ENABLED=false）"
    exit 1
  fi
  _sandbox_entrypoint="$(dirname "$_sandbox_yml")/sandboxd-entrypoint.sh"
  if [[ ! -f "$_sandbox_entrypoint" ]]; then
    echo "ERROR: $_sandbox_yml 需要同目录 sandboxd-entrypoint.sh（或设 GVISOR_ENABLED=false）"
    exit 1
  fi
  # Compose 把 overlay 的 ./ 卷解析到第一个 -f 所在目录（=$DEPLOY）。
  _ep_dst="$DEPLOY/sandboxd-entrypoint.sh"
  if [[ -d "$_ep_dst" ]]; then
    echo "WARN: $_ep_dst 是目录（Docker 缺文件时的占位）— 删除后写入入口脚本"
    rm -rf "$_ep_dst"
  fi
  if [[ "$_sandbox_entrypoint" != "$_ep_dst" ]]; then
    cp -f "$_sandbox_entrypoint" "$_ep_dst"
  fi
  COMPOSE+=(-f "$_sandbox_yml")
  echo "gVisor sandbox overlay: $_sandbox_yml"
fi
"${COMPOSE[@]}" up -d api
echo "api recreated for CORS"

OFFICE_SERVICE=http://127.0.0.1:8090
CONFIG=""
for f in /etc/cloudflared/config.yml /etc/cloudflared/config.yaml \
  /root/.cloudflared/config.yml "$HOME/.cloudflared/config.yml"; do
  [[ -f "$f" ]] && CONFIG="$f" && break
done
if [[ -n "$CONFIG" ]] && grep -q "hostname: ${OFFICE_HOST}" "$CONFIG" \
  && grep -A1 "hostname: ${OFFICE_HOST}" "$CONFIG" | grep -q "${OFFICE_SERVICE}"; then
  echo "cloudflared ingress already ${OFFICE_HOST} → ${OFFICE_SERVICE}"
elif [[ -n "$CONFIG" ]]; then
  python3 - "$CONFIG" "$OFFICE_HOST" "$OFFICE_SERVICE" <<'PY'
import sys, pathlib, re
path, host, service = sys.argv[1:4]
text = pathlib.Path(path).read_text(encoding="utf-8")
block = f"  - hostname: {host}\n    service: {service}\n"
if re.search(rf"hostname:\s*{re.escape(host)}\b", text):
    text = re.sub(
        rf"(  - hostname: {re.escape(host)}\n    service: ).*",
        rf"\g<1>{service}",
        text,
    )
elif "ingress:" in text:
    text = re.sub(r"(ingress:\n)", r"\1" + block, text, count=1)
else:
    sys.exit("no ingress section in cloudflared config")
pathlib.Path(path).write_text(text, encoding="utf-8")
PY
  if systemctl is-active --quiet cloudflared 2>/dev/null; then
    sudo systemctl restart cloudflared
  fi
  echo "cloudflared ingress patched"
else
  echo "WARN: no cloudflared config; skip tunnel ingress patch"
fi
