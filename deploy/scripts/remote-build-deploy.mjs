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
 * 预构建：guest-base:<content-id> 命中后只编 api（约数分钟）；guest 输入变了才
 * 重下 Chromium（小时级）。勿中途杀 SSH；进度可查 check-remote-build.mjs。
 * 切流才停 api。权威 → docs/05-平台与运维/发布与门禁.md
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
# docker-container driver does not inherit dockerd registry-mirrors; syntax
# frontend (docker/dockerfile) and unprefixed FROM resolve docker.io here.
MIRROR="\$(grep -E '^DOCKERHUB_MIRROR=' "\$ENVF" | head -1 | cut -d= -f2-)"
MIRROR="\${MIRROR:-https://docker.m.daocloud.io}"
CFG="\$HOME_DIR/buildkitd.toml"
umask 077
cat > "\$CFG" <<EOF
[registry."docker.io"]
  mirrors = ["\$MIRROR"]
EOF
CFG_HASH="\$(sha256sum "\$CFG" | cut -c1-16)"
MARKER="\$HOME_DIR/.agentcore-cache-config"
if ! docker buildx inspect agentcore-cache >/dev/null 2>&1 || [ "\$(cat "\$MARKER" 2>/dev/null || true)" != "\$CFG_HASH" ]; then
  echo "==> creating docker-container builder agentcore-cache (registry cache; docker.io mirror)"
  docker buildx rm -f agentcore-cache >/dev/null 2>&1 || true
  docker buildx create --name agentcore-cache --driver docker-container --driver-opt network=host --config "\$CFG"
  printf '%s\\n' "\$CFG_HASH" > "\$MARKER"
fi
docker buildx inspect agentcore-cache --bootstrap >/dev/null
`;
}

function prepareBlock() {
  return `echo "==> prepare api:\$SHA (worktree; guest-base + registry cache; no latest)"
${ensureBuildxBlock()}WT="\$HOME_DIR/build-worktrees/\$SHA"
mkdir -p "\$HOME_DIR/build-worktrees"
git worktree prune
if [ -d "\$WT" ]; then
  git worktree remove --force "\$WT" 2>/dev/null || rm -rf "\$WT"
fi
git worktree add --detach "\$WT" "\$SHA"
cleanup_wt() { git -C "\$REPO" worktree remove --force "\$WT" 2>/dev/null || rm -rf "\$WT"; }
trap cleanup_wt EXIT
set -a
# shellcheck disable=SC1091
. "\$WT/apps/server/build-pins.env"
set +a
: "\${PYTHON_IMAGE:?build-pins.env missing PYTHON_IMAGE}"
: "\${PLAYWRIGHT_VERSION:?build-pins.env missing PLAYWRIGHT_VERSION}"
GUEST_ID="\$(
  cd "\$WT/apps/server"
  sha256sum Dockerfile.guest build-pins.env agentcore/tools/sandbox/cloud_python.txt \\
    | sha256sum | cut -c1-16
)"
GUEST_IMAGE="\${IMAGE_REG}/guest-base:\${GUEST_ID}"
CACHE_API="\${IMAGE_REG}/build-cache:api"
CACHE_GUEST="\${IMAGE_REG}/build-cache:guest"
echo "==> guest-base=\$GUEST_IMAGE"
if docker pull "\$GUEST_IMAGE"; then
  echo "==> guest-base cache hit"
else
  echo "==> building guest-base (cold; Playwright/Chromium)"
  docker buildx build --builder agentcore-cache --progress=plain --push --pull \\
    --provenance=false --sbom=false \\
    -t "\$GUEST_IMAGE" \\
    --cache-from "type=registry,ref=\$CACHE_GUEST" \\
    --cache-to "type=registry,ref=\$CACHE_GUEST,mode=max,ignore-error=true" \\
    --build-arg PYTHON_IMAGE="\$PYTHON_IMAGE" \\
    --build-arg PLAYWRIGHT_VERSION="\$PLAYWRIGHT_VERSION" \\
    --build-arg PLAYWRIGHT_DOWNLOAD_HOST="\${PLAYWRIGHT_DOWNLOAD_HOST:-}" \\
    -f "\$WT/apps/server/Dockerfile.guest" \\
    "\$WT/apps/server"
fi
BUILT_AT="\$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "==> building api:\$SHA"
docker buildx build --builder agentcore-cache --progress=plain --push \\
  --provenance=false --sbom=false \\
  -t "\$IMAGE" \\
  --cache-from "type=registry,ref=\$CACHE_API" \\
  --cache-to "type=registry,ref=\$CACHE_API,mode=max,ignore-error=true" \\
  --build-context "guest_image=docker-image://\$GUEST_IMAGE" \\
  --build-arg PYTHON_IMAGE="\$PYTHON_IMAGE" \\
  --build-arg GIT_SHA="\$SHA" --build-arg BUILT_AT="\$BUILT_AT" \\
  -f "\$WT/apps/server/Dockerfile" \\
  "\$WT/apps/server"
echo "==> pulling api:\$SHA onto host for switch inspect"
docker pull "\$IMAGE"
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

/**
 * @param {DeployMode} mode
 * @param {string} sha
 * @returns {string}
 */
export function remoteScript(mode, sha) {
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
    prepare: `→ remote prepare api:${sha}（不切流、不推 latest；guest-base 命中后约数分钟）`,
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
