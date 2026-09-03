#!/usr/bin/env node
/**
 * 后端发布：生产机构建 api 镜像，与切流拆开。
 *
 *   pnpm deploy:backend <sha>                 预构建（默认；不停 api、不推 latest）
 *   pnpm deploy:backend:switch <sha>          切流（finish-server；镜像须已在本机/ACR）
 *   pnpm deploy:backend:now <sha>             紧急：预构建完立刻切流
 *
 * 前置：deploy/.env.deploy.local 配好 DEPLOY_SSH_*；生产机 /opt/agentcore/.env 含 ACR 凭据。
 * 活栈若不在默认 repo/deploy：在 .env.deploy.local 设 AGENTCORE_DEPLOY_DIR（会传入 SSH）。
 * 预构建耗时：层缓存命中约 1–2min；冷缓存 15–30min+。勿中途杀 SSH；进度可查
 * check-remote-build.mjs。切流才停 api。权威 → docs/05-平台与运维/发布与门禁.md
 */
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { loadDeployEnv, sshScript } from "./load-deploy-env.mjs";

const USAGE =
  "usage: node deploy/scripts/remote-build-deploy.mjs [--prepare|--switch|--now] <sha>";

/** @typedef {"prepare" | "switch" | "now"} DeployMode */

/**
 * @param {string[]} rawArgv process.argv.slice(2)
 * @returns {{ mode: DeployMode, sha: string }}
 */
export function parseRemoteDeployArgs(rawArgv) {
  const args = rawArgv.filter((a) => a !== "--").map((a) => a.trim()).filter(Boolean);
  /** @type {DeployMode | null} */
  let mode = null;
  /** @type {string | null} */
  let sha = null;
  for (const a of args) {
    if (a === "--prepare" || a === "--switch" || a === "--now") {
      const next = /** @type {DeployMode} */ (a.slice(2));
      if (mode && mode !== next) {
        throw new Error(`conflicting modes --${mode} and ${a}`);
      }
      mode = next;
      continue;
    }
    if (a.startsWith("-")) {
      throw new Error(`unknown flag ${a}\n${USAGE}`);
    }
    if (sha) {
      throw new Error(`unexpected extra argument '${a}'\n${USAGE}`);
    }
    sha = a;
  }
  if (!sha) {
    throw new Error(USAGE);
  }
  if (!/^[0-9a-fA-F]{7,40}$/.test(sha)) {
    throw new Error(`invalid SHA '${sha}' (expected 7–40 hex chars)`);
  }
  return { mode: mode ?? "prepare", sha };
}

function deployDirExport() {
  const deployDir = process.env.AGENTCORE_DEPLOY_DIR?.trim() || "";
  return deployDir
    ? `export AGENTCORE_DEPLOY_DIR=${JSON.stringify(deployDir)}\n`
    : "";
}

function remotePrelude(sha) {
  return `set -euo pipefail
${deployDirExport()}HOME_DIR="\${AGENTCORE_HOME:-/opt/agentcore}"
REPO="\$HOME_DIR/repo"
SHA="${sha}"
cd "\$REPO"
git fetch --tags --force --quiet origin
git cat-file -t "\$SHA" >/dev/null
ROOT_ENV="\$HOME_DIR/.env"
ACR_USER="\$(grep -E '^ACR_USERNAME=' "\$ROOT_ENV" | head -1 | cut -d= -f2-)"
ACR_PASS="\$(grep -E '^ACR_PASSWORD=' "\$ROOT_ENV" | head -1 | cut -d= -f2-)"
ACR_HOST="\$(grep -E '^ACR_REGISTRY=' "\$ROOT_ENV" | head -1 | cut -d= -f2-)"
ENVF="\${AGENTCORE_DEPLOY_DIR:-\$HOME_DIR/repo/deploy}/config/production.env"
IMAGE_REG="\$(grep -E '^IMAGE_REGISTRY=' "\$ENVF" | head -1 | cut -d= -f2-)"
IMAGE="\${IMAGE_REG}/api:\${SHA}"
echo "==> deploy_dir=\${AGENTCORE_DEPLOY_DIR:-\$HOME_DIR/repo/deploy}"
echo "==> image=\$IMAGE"
echo "\$ACR_PASS" | docker login "\$ACR_HOST" -u "\$ACR_USER" --password-stdin
`;
}

function ensureBuildxBlock() {
  return `if ! docker buildx version >/dev/null 2>&1; then
  echo "==> installing docker-buildx-plugin"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-buildx-plugin
fi
export DOCKER_BUILDKIT=1
`;
}

function prepareBlock() {
  return `echo "==> prepare api:\$SHA (worktree; no checkout of live tree; no latest)"
${ensureBuildxBlock()}WT="\$HOME_DIR/build-worktrees/\$SHA"
mkdir -p "\$HOME_DIR/build-worktrees"
git worktree prune
if [ -d "\$WT" ]; then
  git worktree remove --force "\$WT" 2>/dev/null || rm -rf "\$WT"
fi
git worktree add --detach "\$WT" "\$SHA"
cleanup_wt() { git -C "\$REPO" worktree remove --force "\$WT" 2>/dev/null || rm -rf "\$WT"; }
trap cleanup_wt EXIT
BUILT_AT="\$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker buildx build --progress=plain --load \\
  -t "\$IMAGE" \\
  --build-arg GIT_SHA="\$SHA" --build-arg BUILT_AT="\$BUILT_AT" \\
  "\$WT/apps/server"
docker push "\$IMAGE"
`;
}

function switchBlock() {
  return `echo "==> switch api:\$SHA (finish-server; retag latest after health)"
if ! docker image inspect "\$IMAGE" >/dev/null 2>&1; then
  echo "==> pulling \$IMAGE"
  if ! docker pull "\$IMAGE"; then
    echo "ERROR: api:\$SHA 本机没有且 ACR 拉不到。先 pnpm deploy:backend \$SHA 预构建。"
    exit 1
  fi
fi
echo "==> checkout live tree \$SHA (cutover)"
git checkout "\$SHA"
bash "\$REPO/deploy/scripts/finish-server.sh" "\$SHA"
docker tag "\$IMAGE" "\${IMAGE_REG}/api:latest"
docker push "\${IMAGE_REG}/api:latest"
`;
}

function remoteScript(mode, sha) {
  const prelude = remotePrelude(sha);
  if (mode === "prepare") return prelude + prepareBlock();
  if (mode === "switch") return prelude + switchBlock();
  return (
    prelude +
    prepareBlock() +
    "cleanup_wt || true\ntrap - EXIT\n" +
    switchBlock()
  );
}

function main() {
  let parsed;
  try {
    parsed = parseRemoteDeployArgs(process.argv.slice(2));
  } catch (err) {
    console.error(err instanceof Error ? err.message : err);
    process.exit(1);
  }
  const { mode, sha } = parsed;
  loadDeployEnv();
  const labels = {
    prepare: `→ remote prepare api:${sha}（不切流、不推 latest；层缓存命中约 1–2min）`,
    switch: `→ remote switch api:${sha}（finish-server，不重建）`,
    now: `→ remote prepare+switch api:${sha}（紧急；切流仍在构建之后）`,
  };
  console.log(labels[mode]);
  const deployDir = process.env.AGENTCORE_DEPLOY_DIR?.trim() || "";
  if (deployDir) console.log(`→ AGENTCORE_DEPLOY_DIR=${deployDir}`);
  sshScript(remoteScript(mode, sha));
}

const isMain =
  Boolean(process.argv[1]) &&
  pathToFileURL(resolve(process.argv[1])).href === import.meta.url;

if (isMain) {
  main();
}
