#!/usr/bin/env node
/**
 * Read-only production probe for the platform credential pool.
 * Prints member metadata, spend split, Redis runtime, and pool event counts.
 * Never prints API keys or other secrets.
 */
import { join } from "node:path";
import {
  REPO_ROOT,
  loadDeployEnv,
  scp,
  sshCapture,
} from "./load-deploy-env.mjs";

loadDeployEnv();

const localPy = join(REPO_ROOT, "deploy/scripts/probe-platform-pool.py");
const remotePy = "/tmp/ac-probe-platform-pool.py";

scp(localPy, remotePy);

const remote = `set -euo pipefail
echo "=== version ==="
curl -sf http://127.0.0.1:8000/version || echo "version probe failed"
echo
docker cp ${remotePy} agentcore-api:/tmp/ac-probe-platform-pool.py
docker exec agentcore-api python /tmp/ac-probe-platform-pool.py
echo
echo "=== docker logs pool (excl reload) ==="
docker logs agentcore-api --since 72h 2>&1 \\
  | grep -E "platform_pool\\.|llm\\.rate_limit_no_retry|GoUsageLimit" \\
  | grep -v "platform_pool.reloaded" \\
  | tail -n 120 || true
echo
echo "=== docker logs failover/cooling/blocked counts ==="
docker logs agentcore-api --since 72h 2>&1 \\
  | grep -oE "platform_pool\\.(failover|cooling|blocked|decrypt_failed|reload_failed|redis_fail_open)" \\
  | sort | uniq -c || true
rm -f ${remotePy}
docker exec agentcore-api rm -f /tmp/ac-probe-platform-pool.py || true
`;

const result = sshCapture(remote, { allowFail: true });
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exit(result.status ?? 1);
