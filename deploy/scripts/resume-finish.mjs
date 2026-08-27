#!/usr/bin/env node
/**
 * Skip image build; chown /data/logs then re-run finish-server.sh.
 *
 *   node deploy/scripts/resume-finish.mjs <short-sha>
 *
 * Use after cutover failed with the image already on the machine (e.g. one-shot
 * api user cannot write a root-owned LOG_FILE). Does not rebuild or push.
 *
 * The remote body is written to a tempfile first: `docker compose run` attaches
 * stdin by default, which would otherwise swallow the rest of `ssh bash -s`.
 */
import { loadDeployEnv, sshScript } from "./load-deploy-env.mjs";

loadDeployEnv();

const sha = process.argv[2]?.trim();
if (!sha) {
  console.error("usage: node deploy/scripts/resume-finish.mjs <short-sha>");
  process.exit(1);
}
if (!/^[0-9a-fA-F]{7,40}$/.test(sha)) {
  console.error(`ERROR: invalid SHA '${sha}' (expected 7–40 hex chars)`);
  process.exit(1);
}

const deployDir = process.env.AGENTCORE_DEPLOY_DIR?.trim() || "";
const deployDirExport = deployDir
  ? `export AGENTCORE_DEPLOY_DIR=${JSON.stringify(deployDir)}\n`
  : "";

const script = `set -euo pipefail
${deployDirExport}cat > /tmp/agentcore-resume-finish.sh << 'EOS'
set -euo pipefail
HOME_DIR="\${AGENTCORE_HOME:-/opt/agentcore}"
DEPLOY="\${AGENTCORE_DEPLOY_DIR:-\$HOME_DIR/repo/deploy}"
ENVF="\$DEPLOY/config/production.env"
SHA="\$1"
echo "==> chown /data/logs so one-shot api (user app) can write LOG_FILE"
docker compose -p agentcore \\
  -f "\$DEPLOY/docker-compose.server.yml" \\
  -f "\$DEPLOY/docker-compose.app.yml" \\
  --env-file "\$ENVF" \\
  run --rm --no-deps -T --user 0 api sh -c 'mkdir -p /data/logs && chown -R app:app /data/logs && ls -la /data/logs' </dev/null
echo "==> resume finish-server (skip second workspace snapshot; already taken this window)"
export SKIP_WORKSPACE_SNAPSHOT=1
bash "\$HOME_DIR/repo/deploy/scripts/finish-server.sh" "\$SHA"
EOS
bash /tmp/agentcore-resume-finish.sh ${JSON.stringify(sha)}
`;

console.log(`→ resume finish-server api:${sha} (no rebuild; chown /data/logs first)`);
if (deployDir) console.log(`→ AGENTCORE_DEPLOY_DIR=${deployDir}`);
sshScript(script);
