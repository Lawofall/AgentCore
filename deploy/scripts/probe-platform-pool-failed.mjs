#!/usr/bin/env node
import { join } from "node:path";
import { REPO_ROOT, loadDeployEnv, scp, sshCapture } from "./load-deploy-env.mjs";

loadDeployEnv();
const localPy = join(REPO_ROOT, "deploy/scripts/probe-platform-pool-failed.py");
scp(localPy, "/tmp/ac-probe-pool-failed.py");
const remote = `set -euo pipefail
docker cp /tmp/ac-probe-pool-failed.py agentcore-api:/tmp/ac-probe-pool-failed.py
docker exec agentcore-api python /tmp/ac-probe-pool-failed.py
docker exec agentcore-api rm -f /tmp/ac-probe-pool-failed.py || true
rm -f /tmp/ac-probe-pool-failed.py || true
`;
const result = sshCapture(remote, { allowFail: true });
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exit(result.status ?? 1);
