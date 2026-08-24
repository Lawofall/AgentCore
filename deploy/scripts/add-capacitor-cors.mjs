#!/usr/bin/env node
import { loadDeployEnv, sshScript } from "./load-deploy-env.mjs";

loadDeployEnv();

sshScript(`
set -euo pipefail
HOME_ROOT=/opt/agentcore
WD=/opt/agentcore/repo/deploy_f6d1637
ENVF="\$WD/config/production.env"

# Show which registry-related keys exist (values ALWAYS redacted)
python3 - "\$HOME_ROOT/.env" <<'PY'
from pathlib import Path
import sys
for line in Path(sys.argv[1]).read_text().splitlines():
    if not line or line.startswith('#') or '=' not in line:
        continue
    k, v = line.split('=', 1)
    k = k.strip()
    if any(x in k.upper() for x in ('IMAGE', 'ACR', 'REGISTRY', 'TAG', 'PASSWORD', 'SECRET', 'TOKEN', 'KEY')):
        print(f"{k}=set(len={len(v.strip())})")
PY

# Derive IMAGE_REGISTRY if missing
set -a
# shellcheck disable=SC1091
. "\$HOME_ROOT/.env"
set +a
if [[ -z "\${IMAGE_REGISTRY:-}" && -n "\${ACR_REGISTRY:-}" ]]; then
  # typical: registry/namespace
  export IMAGE_REGISTRY="\$ACR_REGISTRY/agentcore1"
  echo "derived IMAGE_REGISTRY=\$IMAGE_REGISTRY"
fi
echo "IMAGE_TAG=\${IMAGE_TAG:-latest}"
echo "resolved image=\${IMAGE_REGISTRY:-agentcore}/api:\${IMAGE_TAG:-latest}"

# Confirm docker has that image
docker image inspect "\${IMAGE_REGISTRY:-agentcore}/api:\${IMAGE_TAG:-latest}" >/dev/null 2>&1 && echo image_ok || {
  echo "image missing; tagging from running container"
  CID=\$(docker inspect -f '{{.Id}}' agentcore-api)
  docker tag "\$CID" "\${IMAGE_REGISTRY:-agentcore}/api:\${IMAGE_TAG:-latest}"
  docker tag "\$CID" "agentcore/api:latest" || true
  echo tagged
}

CFG="\$WD/docker-compose.server.yml,\$WD/docker-compose.app.yml,/opt/agentcore/repo/deploy/docker-compose.sandbox.yml"
IFS=',' read -ra FILES <<< "\$CFG"
ARGS=()
for f in "\${FILES[@]}"; do ARGS+=(-f "\$f"); done

# Export IMAGE_REGISTRY into compose env file temporarily? safer: pass as env on the command
export IMAGE_REGISTRY="\${IMAGE_REGISTRY}"
export IMAGE_TAG="\${IMAGE_TAG:-latest}"

(cd "\$WD" && \\
  IMAGE_REGISTRY="\$IMAGE_REGISTRY" IMAGE_TAG="\$IMAGE_TAG" \\
  docker compose -p agentcore "\${ARGS[@]}" --env-file "\$HOME_ROOT/.env" \\
    up -d --no-deps --no-build --pull never --force-recreate api)

for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -fsS http://127.0.0.1:8000/readyz >/dev/null 2>&1; then
    echo api_ready
    docker inspect agentcore-api --format '{{range .Config.Env}}{{println .}}{{end}}' \\
      | grep '^CORS_ALLOW_ORIGINS=' \\
      | python3 -c "import sys; v=sys.stdin.read().split('=',1)[-1]; print('https://localhost', 'https://localhost' in v); print('capacitor', 'capacitor://localhost' in v); print('http_localhost', 'http://localhost' in v)"
    exit 0
  fi
  sleep 2
done
docker ps -a --filter name=agentcore-api --format '{{.Names}} {{.Status}} {{.Image}}'
docker logs agentcore-api --tail 40 || true
exit 1
`);
