#!/usr/bin/env node
import { join } from "node:path";
import { REPO_ROOT, loadDeployEnv, scp, sshCapture } from "./load-deploy-env.mjs";

loadDeployEnv();
const localPy = join(REPO_ROOT, "deploy/scripts/probe-platform-pool-logs.py");
scp(localPy, "/tmp/ac-probe-pool-logs.py");
const remote = `set -euo pipefail
docker cp /tmp/ac-probe-pool-logs.py agentcore-api:/tmp/ac-probe-pool-logs.py
docker exec agentcore-api python /tmp/ac-probe-pool-logs.py
docker exec agentcore-api rm -f /tmp/ac-probe-pool-logs.py || true
rm -f /tmp/ac-probe-pool-logs.py || true
`;
const result = sshCapture(remote, { allowFail: true });
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exit(result.status ?? 1);
