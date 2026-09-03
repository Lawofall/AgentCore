/**
 * Load deploy secrets from local env files (gitignored). Later files do not override earlier keys.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dir = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = join(__dir, "../..");

const ENV_FILES = [
  join(REPO_ROOT, "deploy/.env.deploy.local"),
  join(REPO_ROOT, "apps/website/.env.deploy.local"),
];

function loadDotEnv(path) {
  if (!existsSync(path)) return;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let val = trimmed.slice(eq + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = val;
  }
}

export function loadDeployEnv() {
  for (const file of ENV_FILES) {
    loadDotEnv(file);
  }
}

export function requireEnv(name) {
  const val = process.env[name]?.trim();
  if (!val) {
    console.error(`Missing ${name} — set in deploy/.env.deploy.local`);
    process.exit(1);
  }
  return val;
}

/** Quote for cmd.exe when `shell: true` concatenates argv (spaces in Program Files). */
function winShellQuote(value) {
  const s = String(value);
  if (!/[ \t"]/.test(s)) return s;
  return `"${s.replace(/"/g, '\\"')}"`;
}

export function run(label, cmd, args, opts = {}) {
  console.log(`→ ${label}`);
  const useShell = process.platform === "win32";
  const result = spawnSync(
    useShell ? winShellQuote(cmd) : cmd,
    useShell ? args.map(winShellQuote) : args,
    {
      cwd: opts.cwd ?? REPO_ROOT,
      stdio: opts.input ? ["pipe", "inherit", "inherit"] : (opts.stdio ?? "inherit"),
      env: opts.env ?? process.env,
      input: opts.input,
      shell: useShell,
    },
  );
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

const WRANGLER_PAGES_DEPLOY = join(__dir, "wrangler-pages-deploy.mjs");

/** Upload a dist folder to Cloudflare Pages via a fresh Node child (Windows-safe). */
export function runWranglerPagesDeploy(project, distPath, { branch = "main" } = {}) {
  console.log(`→ wrangler pages deploy → ${project} (subprocess)`);
  const result = spawnSync(
    process.execPath,
    [WRANGLER_PAGES_DEPLOY, project, distPath, "--branch", branch],
    { cwd: REPO_ROOT, stdio: "inherit", env: process.env, shell: false },
  );
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

/** Cloudflare Account ID from env (after loadDeployEnv). Missing → exit with hint. */
export function resolveCfAccountId() {
  return requireEnv("CLOUDFLARE_ACCOUNT_ID");
}

export function cfEnv() {
  const token = requireEnv("CLOUDFLARE_API_TOKEN");
  const accountId = resolveCfAccountId();
  return {
    ...process.env,
    CLOUDFLARE_API_TOKEN: token,
    CLOUDFLARE_ACCOUNT_ID: accountId,
  };
}

export function sshArgs() {
  const host = requireEnv("DEPLOY_SSH_HOST");
  const user = requireEnv("DEPLOY_SSH_USER");
  const port = process.env.DEPLOY_SSH_PORT?.trim() || "22";
  const keyPath =
    process.env.DEPLOY_SSH_KEY_PATH?.trim() ||
    process.env.DEPLOY_SSH_KEY?.trim();
  if (!keyPath) {
    console.error(
      "Missing DEPLOY_SSH_KEY_PATH — path to SSH private key for production server",
    );
    process.exit(1);
  }
  if (!existsSync(keyPath)) {
    console.error(`SSH key not found: ${keyPath}`);
    process.exit(1);
  }
  return { host, user, port, keyPath };
}

export function scp(localPath, remotePath) {
  const { host, user, port, keyPath } = sshArgs();
  run(`scp ${localPath}`, "scp", [
    "-i",
    keyPath,
    "-P",
    port,
    "-o",
    "StrictHostKeyChecking=accept-new",
    localPath,
    `${user}@${host}:${remotePath}`,
  ]);
}

/** Pull a remote file onto the local machine (inverse of {@link scp}). */
export function scpFrom(remotePath, localPath) {
  const { host, user, port, keyPath } = sshArgs();
  run(`scp ← ${remotePath}`, "scp", [
    "-i",
    keyPath,
    "-P",
    port,
    "-o",
    "StrictHostKeyChecking=accept-new",
    `${user}@${host}:${remotePath}`,
    localPath,
  ]);
}

export function sshScript(scriptText) {
  const { host, user, port, keyPath } = sshArgs();
  run("ssh remote script", "ssh", [
    "-i",
    keyPath,
    "-p",
    port,
    "-o",
    "StrictHostKeyChecking=accept-new",
    `${user}@${host}`,
    "bash -s",
  ], { input: scriptText });
}

/**
 * Run a remote bash script via stdin and capture utf-8 stdout/stderr.
 * Does not inherit stdio — use for discovery probes, not long progress dumps.
 */
export function sshCapture(scriptText, { allowFail = false } = {}) {
  const { host, user, port, keyPath } = sshArgs();
  const useShell = process.platform === "win32";
  const args = [
    "-i",
    keyPath,
    "-p",
    port,
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
    `${user}@${host}`,
    "bash -s",
  ];
  const result = spawnSync(
    useShell ? winShellQuote("ssh") : "ssh",
    useShell ? args.map(winShellQuote) : args,
    {
      cwd: REPO_ROOT,
      encoding: "utf8",
      input: scriptText,
      shell: useShell,
      env: process.env,
    },
  );
  const status = result.status ?? 1;
  const stdout = (result.stdout ?? "").replace(/\r\n/g, "\n");
  const stderr = (result.stderr ?? "").replace(/\r\n/g, "\n");
  if (status !== 0 && !allowFail) {
    if (stderr.trim()) console.error(stderr.trimEnd());
    if (stdout.trim()) console.error(stdout.trimEnd());
    process.exit(status);
  }
  return { status, stdout, stderr };
}

/** Run a git command from REPO_ROOT and capture trimmed stdout. */
function git(args) {
  const r = spawnSync("git", args, {
    cwd: REPO_ROOT,
    encoding: "utf8",
    shell: process.platform === "win32",
  });
  return { ok: r.status === 0, out: (r.stdout ?? "").trim() };
}

/** True when `sha` (full or short) resolves to a local commit object. */
function isLocalCommit(sha) {
  const r = git(["cat-file", "-t", sha]);
  return r.ok && r.out === "commit";
}

/**
 * Deploy-time guard against 前后端版本漂移 — a FRONTEND deployed AHEAD of the backend.
 * A newer client calls endpoints the older *deployed* backend lacks → 404 (e.g. the
 * 记忆·主题 incident: web shipped the 主题 UI while prod backend预 `/topics`). All three
 * SPAs (web client / mobile / admin) hit the same API, so they share this check.
 *
 * Compares the commit being deployed (git HEAD) against the LIVE backend's git_sha
 * (`GET <apiBaseUrl>/version`). Ships only when the backend already CONTAINS this
 * commit — i.e. HEAD is an ancestor of the backend sha (backend == or newer), so every
 * endpoint this build calls exists server-side. A strictly-newer / diverged frontend is
 * hard-blocked with how to fix; pass `--force` (or `DEPLOY_SKIP_CONTRACT_GATE=1`) to
 * override for a vetted frontend-only change.
 *
 * Fails OPEN (warn + allow) ONLY when it genuinely can't compare — backend git_sha
 * unknown, the /version probe failed, or the sha isn't in local history after a fetch —
 * so a flaky probe never blocks a deploy while the real "frontend ahead" case is caught.
 */
export async function assertBackendContractSatisfied({ apiBaseUrl, force = false }) {
  const skip =
    force ||
    process.argv.includes("--force") ||
    process.env.DEPLOY_SKIP_CONTRACT_GATE === "1";

  // Placeholder docs hosts must never ship — fail closed even when /version is
  // unreachable (that path otherwise fail-opens and would bake app.example.com).
  // Intentional fork/test: DEPLOY_ALLOW_PLACEHOLDER_API=1.
  let apiHost;
  try {
    apiHost = new URL(apiBaseUrl).hostname;
  } catch {
    apiHost = "";
  }
  const isPlaceholderHost =
    apiHost === "example.com" || apiHost.endsWith(".example.com");
  if (isPlaceholderHost && process.env.DEPLOY_ALLOW_PLACEHOLDER_API !== "1") {
    console.error(
      [
        "",
        "✖ 部署被拦截：API 基址仍是文档占位域名。",
        `  apiBaseUrl: ${apiBaseUrl}`,
        "  请在 deploy/.env.deploy.local 设 AGENTCORE_APP_API_URL / AGENTCORE_APP_HOST",
        "  （或依赖 apps/*/ .env.production），勿把 app.example.com 烤进产物。",
        "  确需用占位域做演练：设 DEPLOY_ALLOW_PLACEHOLDER_API=1。",
        "",
      ].join("\n"),
    );
    process.exit(1);
  }

  const head = git(["rev-parse", "HEAD"]);
  if (!head.ok) {
    console.warn("⚠ 契约门禁：无法解析 git HEAD — 跳过校验");
    return;
  }
  const webSha = head.out;

  let backendSha;
  try {
    const res = await fetch(`${apiBaseUrl}/version`, {
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    backendSha = (await res.json()).git_sha;
  } catch (e) {
    console.warn(
      `⚠ 契约门禁：读不到 ${apiBaseUrl}/version（${e.message}）— 跳过校验`,
    );
    return;
  }

  if (!backendSha || backendSha === "unknown") {
    console.warn("⚠ 契约门禁：后端 /version 的 git_sha 为 unknown — 跳过校验");
    return;
  }

  // Ancestry needs the backend sha in local history; fetch once if it's missing.
  // Use `cat-file -t` (not `sha^{commit}`): on Windows `git()` runs with `shell:true`,
  // and cmd.exe treats `^` as an escape — so `bafda4f9^{commit}` becomes
  // `bafda4f9{commit}` and a short sha that IS HEAD falsely "isn't in history".
  if (!isLocalCommit(backendSha)) {
    git(["fetch", "--quiet", "origin"]);
    if (!isLocalCommit(backendSha)) {
      console.warn(
        `⚠ 契约门禁：后端 sha ${backendSha} 不在本地 git 历史里（先 git fetch）— 跳过校验`,
      );
      return;
    }
  }

  // Safe iff the backend already contains this commit (HEAD ⊆ backend).
  if (git(["merge-base", "--is-ancestor", webSha, backendSha]).ok) {
    console.log(
      `✓ 契约门禁：线上后端 ${backendSha} 已含本次构建 ${webSha.slice(0, 7)}，放行`,
    );
    return;
  }

  const msg = [
    "",
    "✖ 部署被拦截：前端比线上后端新（前后端版本漂移）。",
    `  本次构建 HEAD    : ${webSha.slice(0, 7)}`,
    `  线上后端 /version: ${backendSha}`,
    "  前端可能调用后端还没有的接口（如 记忆·主题 → 404）。",
    "  先切流后端：pnpm deploy:backend:switch <short-sha>（须已预构建），待 /api/version 追上后再发前端。",
    "  确需强发（纯前端改动、确认无新接口）：加 --force 或设 DEPLOY_SKIP_CONTRACT_GATE=1。",
    "",
  ].join("\n");
  if (skip) {
    console.warn(msg);
    console.warn("⚠ 已设 --force / DEPLOY_SKIP_CONTRACT_GATE：跳过拦截，继续部署。");
    return;
  }
  console.error(msg);
  process.exit(1);
}
