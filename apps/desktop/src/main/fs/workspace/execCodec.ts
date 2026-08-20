/**
 * Pipe decode + Windows bash launcher resolution for local ``execute``.
 * Mirrors ``agentcore.tools.sandbox.subprocess`` so desktop channel and sidecar
 * refuse the same WSL trampoline and re-decode UTF-16LE noise the same way.
 */

import { existsSync } from "node:fs";
import path from "node:path";

/** Match ``process.platform`` so win32 probes work when tests fake platform on Linux CI. */
function pathApi() {
  return process.platform === "win32" ? path.win32 : path;
}

/** NUL density at/above this → treat the chunk as UTF-16LE (ASCII-range text). */
const NUL_DENSITY_UTF16 = 0.3;

/** Injectable for tests (WSL trampoline / Git Bash path existence). */
let pathExists: (p: string) => boolean = existsSync;

/** @internal test-only */
export function _setPathExistsForTests(
  fn: ((p: string) => boolean) | null,
): void {
  pathExists = fn ?? existsSync;
}

/**
 * Decode a captured subprocess chunk. WSL / some Win32 tools emit UTF-16LE;
 * naive UTF-8 yields ``w\\0s\\0l\\0…`` that poisons the model.
 */
export function decodePipeChunk(chunk: Buffer): string {
  if (chunk.length >= 4) {
    let nuls = 0;
    for (let i = 0; i < chunk.length; i++) {
      if (chunk[i] === 0) nuls++;
    }
    if (nuls / chunk.length >= NUL_DENSITY_UTF16) {
      return chunk.toString("utf16le");
    }
  }
  return chunk.toString("utf-8");
}

/** True when ``path`` is the Windows System32/SysWOW64 WSL bash trampoline. */
export function isWslBashTrampoline(path: string): boolean {
  const norm = path.replace(/\//g, "\\").toLowerCase();
  return (
    norm.endsWith("\\system32\\bash.exe") ||
    norm.endsWith("\\syswow64\\bash.exe")
  );
}

function gitBashCandidates(): string[] {
  const out: string[] = [
    "C:\\Program Files\\Git\\bin\\bash.exe",
    "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
  ];
  const local = process.env.LOCALAPPDATA;
  if (local) {
    out.push(pathApi().join(local, "Programs", "Git", "bin", "bash.exe"));
  }
  return out;
}

/** Every ``bash`` / ``bash.exe`` hit on PATH (order preserved). */
function whichAllBash(): string[] {
  const { join, delimiter } = pathApi();
  const pathEnv = process.env.PATH ?? process.env.Path ?? "";
  const names = process.platform === "win32" ? ["bash.exe", "bash"] : ["bash"];
  const found: string[] = [];
  const seen = new Set<string>();
  for (const dir of pathEnv.split(delimiter)) {
    if (!dir) continue;
    for (const name of names) {
      const candidate = join(dir, name);
      const key = candidate.toLowerCase();
      if (seen.has(key)) continue;
      if (!pathExists(candidate)) continue;
      seen.add(key);
      found.push(candidate);
    }
  }
  return found;
}

/**
 * Resolve a usable bash binary.
 * Windows: prefer Git Bash; skip System32 WSL trampoline; else ``null`` (honest reject).
 * Non-Windows: first ``bash`` on PATH, or ``null``.
 */
export function resolveBashLauncher(): string | null {
  if (process.platform !== "win32") {
    const hits = whichAllBash();
    return hits[0] ?? null;
  }
  for (const p of gitBashCandidates()) {
    if (pathExists(p)) return p;
  }
  for (const p of whichAllBash()) {
    if (!isWslBashTrampoline(p)) return p;
  }
  return null;
}

/** Chinese guidance when bash cannot be launched (aligned with sidecar). */
export const BASH_UNAVAILABLE_HINT =
  "本机没有可用的 bash（Windows 上 PATH 的 bash 常是不可用的 WSL 蹦床）。" +
  "请改用 language=javascript 或 python 直接跑代码，不要用 bash 外壳包一层。";

/** Byte-equal with ``agentcore.tools.sandbox.exec_env`` spawn-site contract. */
export const EXEC_ENV_PROBE_FAIL_MARKER = "ExecEnvProbeFailed:";
export const EXEC_ENV_SPAWN_DENIED_CODE = "exec_env_spawn_denied";

/** True when Node's spawn errno is a refused start (not a missing binary). */
export function isSpawnDeniedError(err: unknown): boolean {
  const code = (err as NodeJS.ErrnoException | undefined)?.code;
  return code === "EACCES" || code === "EPERM";
}

/**
 * Thin spawn-site envelope: marker + reason tag + the OS error we caught.
 * Server ``annotate_real_exec_failure`` keys on the tag, never on OS prose.
 */
export function spawnDeniedStderr(detail: string): string {
  const trimmed = detail.trim();
  return trimmed
    ? `${EXEC_ENV_PROBE_FAIL_MARKER} [${EXEC_ENV_SPAWN_DENIED_CODE}] ${trimmed}`
    : `${EXEC_ENV_PROBE_FAIL_MARKER} [${EXEC_ENV_SPAWN_DENIED_CODE}]`;
}

export function launcherMissingStderr(
  launcher: string,
  language: string,
): string {
  if (language === "bash") {
    return `代码执行环境启动失败：找不到可用的命令 ${JSON.stringify(launcher)}。 ${BASH_UNAVAILABLE_HINT}`;
  }
  if (language === "python") {
    return `代码执行环境启动失败：找不到命令 ${JSON.stringify(launcher)}。 请确认 PATH 上有 python 可执行文件。`;
  }
  if (language === "javascript") {
    return `代码执行环境启动失败：找不到命令 ${JSON.stringify(launcher)}。 请确认 PATH 上有 node 可执行文件。`;
  }
  return `代码执行环境启动失败：找不到命令 ${JSON.stringify(launcher)}。`;
}

/** First PATH hit for ``name`` (``node`` / ``python``), or ``null``. */
export function whichCommand(name: string): string | null {
  const { join, delimiter } = pathApi();
  const pathEnv = process.env.PATH ?? process.env.Path ?? "";
  const names =
    process.platform === "win32"
      ? [`${name}.exe`, name, `${name}.cmd`, `${name}.bat`]
      : [name];
  for (const dir of pathEnv.split(delimiter)) {
    if (!dir) continue;
    for (const n of names) {
      const candidate = join(dir, n);
      if (pathExists(candidate)) return candidate;
    }
  }
  return null;
}

export type ExecLanguage = "python" | "javascript" | "bash";

const ALL_EXEC_LANGUAGES: readonly ExecLanguage[] = [
  "python",
  "javascript",
  "bash",
];

/**
 * Probe which ``code_execute`` languages have a usable launcher on this host.
 * Mirrors server ``probe_available_languages`` / ``_resolve_language_cmd``.
 */
export function probeAvailableLanguages(): ExecLanguage[] {
  const out: ExecLanguage[] = [];
  for (const lang of ALL_EXEC_LANGUAGES) {
    if (lang === "bash") {
      if (resolveBashLauncher()) out.push(lang);
      continue;
    }
    const bin = lang === "javascript" ? "node" : "python";
    if (whichCommand(bin)) out.push(lang);
  }
  return out;
}
