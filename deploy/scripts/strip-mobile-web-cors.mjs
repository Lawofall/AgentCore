#!/usr/bin/env node
/**
 * 从生产 CORS_ALLOW_ORIGINS 去掉已下线的手机网页 origin，并 recreate api。
 * 不打印 origin / 凭据。Capacitor localhost 三项必须仍在。
 *
 *   node deploy/scripts/strip-mobile-web-cors.mjs
 */
import { loadDeployEnv, sshScript } from "./load-deploy-env.mjs";

loadDeployEnv();

const deployDir = process.env.AGENTCORE_DEPLOY_DIR?.trim() || "";
const deployDirExport = deployDir
  ? `export AGENTCORE_DEPLOY_DIR=${JSON.stringify(deployDir)}\n`
  : "";

const py = String.raw`
from pathlib import Path
import sys

NEEDLE = "m.fashitianxia"
KEEP = ("https://localhost", "capacitor://localhost", "http://localhost")

def load_cors(path):
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("CORS_ALLOW_ORIGINS="):
            return line.split("=", 1)[1]
    return None

def strip(val):
    parts = [p.strip() for p in val.split(",") if p.strip()]
    kept, removed = [], False
    for p in parts:
        if NEEDLE in p.lower():
            removed = True
            continue
        kept.append(p)
    return ",".join(kept), removed

def rewrite(path):
    if not path.is_file():
        return "missing"
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    changed = False
    removed = False
    out = []
    for line in lines:
        raw = line.rstrip("\r\n")
        nl = line[len(raw):]
        if not raw.startswith("CORS_ALLOW_ORIGINS="):
            out.append(line)
            continue
        new_val, removed = strip(raw.split("=", 1)[1])
        for origin in KEEP:
            if origin not in new_val.split(","):
                raise SystemExit("refusing to write %s: missing %s" % (path.name, origin))
        new_line = "CORS_ALLOW_ORIGINS=" + new_val + nl
        if new_line != line:
            changed = True
        out.append(new_line)
    if changed:
        path.write_text("".join(out), encoding="utf-8")
    return "removed=%s wrote=%s" % (removed, changed)

seen = []
for raw in sys.argv[1:]:
    p = Path(raw)
    key = str(p.resolve()) if p.exists() else str(p)
    if key in seen:
        print("%s: alias" % p.name)
        continue
    seen.append(key)
    before = load_cors(p)
    print("%s: present=%s mobile_origin=%s" % (
        p.name,
        before is not None,
        bool(before and NEEDLE in before.lower()),
    ))
    print("%s: %s" % (p.name, rewrite(p)))
`;

sshScript(`
set -euo pipefail
${deployDirExport}HOME_DIR="\${AGENTCORE_HOME:-/opt/agentcore}"
DEPLOY="\${AGENTCORE_DEPLOY_DIR:-\$HOME_DIR/repo/deploy}"
ENVF="\$DEPLOY/config/production.env"
ROOT_ENV="\$HOME_DIR/.env"

WD=\$(docker inspect agentcore-api --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null || true)
if [[ -n "\$WD" && -f "\$WD/config/production.env" ]]; then
  LIVE_ENVF="\$WD/config/production.env"
else
  LIVE_ENVF="\$ENVF"
fi

echo "envf_exists=\$( [[ -f \"\$ENVF\" ]] && echo yes || echo no )"
echo "live_envf_exists=\$( [[ -f \"\$LIVE_ENVF\" ]] && echo yes || echo no )"
echo "home_env_exists=\$( [[ -f \"\$ROOT_ENV\" ]] && echo yes || echo no )"

python3 - "\$ENVF" "\$LIVE_ENVF" "\$ROOT_ENV" <<'PY'
${py}
PY

COMPOSE_BASE=( docker compose -p agentcore -f "\$DEPLOY/docker-compose.server.yml" -f "\$DEPLOY/docker-compose.app.yml" --env-file "\$ENVF" )
if [[ -n "\$WD" && -f "\$WD/docker-compose.server.yml" ]]; then
  COMPOSE_BASE=( docker compose -p agentcore -f "\$WD/docker-compose.server.yml" -f "\$WD/docker-compose.app.yml" --env-file "\$LIVE_ENVF" )
fi

echo "recreate_api"
(cd "\${WD:-\$DEPLOY}" && "\${COMPOSE_BASE[@]}" up -d --no-deps --no-build --pull never --force-recreate api)

for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -fsS http://127.0.0.1:8000/readyz >/dev/null 2>&1; then
    echo api_ready
    docker inspect agentcore-api --format '{{range .Config.Env}}{{println .}}{{end}}' \\
      | python3 -c "import sys
v=''.join(l.split('=',1)[-1] for l in sys.stdin if l.startswith('CORS_ALLOW_ORIGINS='))
print('container_mobile_origin=' + str('m.fashitianxia' in v))
print('container_localhost=' + str('https://localhost' in v))
print('container_capacitor=' + str('capacitor://localhost' in v))
print('container_http_localhost=' + str('http://localhost' in v))"
    exit 0
  fi
  sleep 2
done
echo api_not_ready
docker ps -a --filter name=agentcore-api --format '{{.Names}} {{.Status}}'
exit 1
`);
