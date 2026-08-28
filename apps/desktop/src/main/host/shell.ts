import { spawn } from "node:child_process";
import type { HostOpResult } from "@shared/host-contract";
import {
  buildHostShellEnv,
  fingerprintShellEnv,
  looksLikeGuiLaunch,
  snapshotVisibleMainWindows,
} from "../host-shell-obs";
import { logDesktop } from "../log-service";
import { killProcessTree, treeSpawnOptions } from "../proc-tree";
import { type HostShellCwdHint, resolveHostShellCwd } from "./cwd";
import { err, ok } from "./result";

/** P3 host_shell timeout clamp (seconds). */
const SHELL_TIMEOUT_DEFAULT = 60;
const SHELL_TIMEOUT_MAX = 120;
const SHELL_OUTPUT_MAX = 200_000;

/**
 * Heuristic fuse — not a complete security boundary (Host 定案 P3).
 * Keep in rough lockstep with server ``shell_fuse_blocks``.
 */
const SHELL_FUSE_PATTERNS: RegExp[] = [
  /\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|-[a-zA-Z]*r[a-zA-Z]*\s+)*(\/|\/\*|~|\/home)\b/i,
  /\brm\s+-rf\s+\//i,
  /\bformat\s+[a-z]:/i,
  /\bFormat-Volume\b/i,
  /\bClear-Disk\b/i,
  /\b(shutdown|poweroff|reboot|halt)\b/i,
  /\bStop-Computer\b/i,
  /\bRestart-Computer\b/i,
  /\bmkfs(\.\w+)?\b/i,
  /\bdd\s+.*\bof\s*=\s*\/dev\//i,
  /\bdel\s+\/[sq]\s+[a-z]:\\?\s*$/i,
  /\bRemove-Item\b.*-[Rr]ecurse.*[Cc]:\\/i,
  /:\(\)\s*\{\s*:\|:&\s*\}\s*;/,
  /\bcipher\s+\/w:/i,
];

/**
 * Silent / unattended installer heuristics — not a complete boundary (桶4).
 * Keep in rough lockstep with server ``shell_silent_install_blocks``.
 */
const SHELL_SILENT_INSTALL_PATTERNS: RegExp[] = [
  /\bmsiexec\b.*(?:\/quiet|\/qn\b|\/passive\b)/i,
  /\bStart-Process\b[\s\S]{0,200}(?:\/[Ss]\b|\/silent\b|\/quiet\b|\/qn\b|\/verysilent\b)/i,
  /\.(?:exe|msi)\b[^\n]{0,120}(?:\/[Ss]\b|\/silent\b|\/verysilent\b|\/quiet\b|\/qn\b)/i,
  /\/VERYSILENT\b/i,
  /\b(?:curl|wget|Invoke-WebRequest)\b[\s\S]{0,160}\.(?:exe|msi)\b/i,
];

function shellFuseBlocks(command: string): string | null {
  const text = command.trim();
  if (!text) return null;
  for (const pat of SHELL_FUSE_PATTERNS) {
    if (pat.test(text)) {
      return (
        "host_shell 熔断：命令匹配毁灭性启发式黑名单（格式化磁盘 / " +
        "rm -rf / / shutdown 等）。此为兜底、非完整安全边界。"
      );
    }
  }
  return null;
}

export function shellSilentInstallBlocks(command: string): string | null {
  const text = command.trim();
  if (!text) return null;
  for (const pat of SHELL_SILENT_INSTALL_PATTERNS) {
    if (pat.test(text)) {
      return (
        "host_shell 熔断：命令匹配静默安装启发式（msiexec /quiet、Setup /S、" +
        "Start-Process quiet 等）。此为启发式兜底，并非完整拦截；" +
        "请改用结构化 host_package_install（winget/brew/apt 点名包）。"
      );
    }
  }
  return null;
}

/**
 * Refuse cmd/bash idioms that break under Windows PowerShell host_shell.
 * Keep in rough lockstep with server ``shell_cmd_env_blocks`` (+ win32 ||/&&).
 */
function shellPowershellIdiomBlocks(command: string): string | null {
  if (/%[A-Za-z_][A-Za-z0-9_]*%/.test(command)) {
    return (
      "host_shell 在 Windows 上走 PowerShell，不会展开 cmd 风格 %VAR%。" +
      "请改用 $env:APPDATA / $env:LOCALAPPDATA / $env:USERPROFILE 等；" +
      "路径含空格时加引号。"
    );
  }
  if (
    process.platform === "win32" &&
    (command.includes("||") || command.includes("&&"))
  ) {
    return (
      "Windows host_shell 是 PowerShell：不支持 bash/cmd 的 || / && 链式。" +
      "请用 `;` 分隔，或 `if (...) { }`，或拆成多次 host_shell。"
    );
  }
  return null;
}

export function clampShellTimeout(raw: unknown): number {
  if (raw === undefined || raw === null || raw === "")
    return SHELL_TIMEOUT_DEFAULT;
  const n = typeof raw === "number" ? raw : Number.parseInt(String(raw), 10);
  if (!Number.isFinite(n)) return SHELL_TIMEOUT_DEFAULT;
  return Math.max(1, Math.min(SHELL_TIMEOUT_MAX, Math.trunc(n)));
}

function truncateOut(s: string): string {
  if (s.length <= SHELL_OUTPUT_MAX) return s;
  return `${s.slice(0, SHELL_OUTPUT_MAX)}\n…[truncated]`;
}

export async function hostShell(
  command: string,
  timeoutSeconds: number,
  hint: HostShellCwdHint = {},
): Promise<HostOpResult> {
  const cmd = command.trim();
  if (!cmd) {
    return err("command is required", "HostShellEmptyCommand");
  }
  const fuse = shellFuseBlocks(cmd);
  if (fuse) {
    return err(fuse, "HostShellFuse");
  }
  const silent = shellSilentInstallBlocks(cmd);
  if (silent) {
    return err(silent, "HostShellSilentInstall");
  }
  const idiom = shellPowershellIdiomBlocks(cmd);
  if (idiom) {
    return err(idiom, "HostShellIdiom");
  }
  const cwdPick = await resolveHostShellCwd(hint);
  if (!cwdPick.ok) {
    return err(cwdPick.error, "HostShellCwdDenied");
  }
  const cwd = cwdPick.cwd;
  const timeoutMs = timeoutSeconds * 1000;

  let file: string;
  let args: string[];
  if (process.platform === "win32") {
    file = "powershell.exe";
    args = ["-NoProfile", "-NonInteractive", "-Command", cmd];
  } else {
    const sh = (process.env.SHELL || "").trim() || "/bin/bash";
    file = sh;
    args = ["-lc", cmd];
  }

  // 隔离：剥掉 Electron/vite 开发身份，避免 Start-Process 把本产品前端灌进其它 App。
  const { env: childEnv, stripped_keys } = buildHostShellEnv(process.env);
  const obs_env_parent = fingerprintShellEnv(process.env);
  const obs_env = fingerprintShellEnv(childEnv);
  logDesktop({
    level: "info",
    event: "desktop.host_shell_env_fingerprint",
    fields: {
      stripped_key_count: stripped_keys.length,
      stripped_keys,
      parent_matching_keys: obs_env_parent.matching_keys,
      parent_safe_values: obs_env_parent.safe_values,
      parent_electron_renderer_url_set:
        obs_env_parent.electron_renderer_url_set,
      child_matching_keys: obs_env.matching_keys,
      child_safe_values: obs_env.safe_values,
      child_electron_renderer_url_set: obs_env.electron_renderer_url_set,
      gui_launch: looksLikeGuiLaunch(cmd),
    },
  });

  return new Promise((resolve) => {
    const child = spawn(file, args, {
      cwd,
      windowsHide: true,
      env: childEnv,
      ...treeSpawnOptions(),
    });
    let stdout = "";
    let stderr = "";
    let settled = false;

    const finishOk = (base: Record<string, unknown>) => {
      void (async () => {
        const value: Record<string, unknown> = {
          ...base,
          obs_env,
          obs_env_stripped_keys: stripped_keys,
        };
        if (looksLikeGuiLaunch(cmd)) {
          const obs_windows = await snapshotVisibleMainWindows();
          value.obs_windows = obs_windows;
          logDesktop({
            level: "info",
            event: "desktop.host_shell_windows_snapshot",
            fields: {
              count: obs_windows.length,
              windows: obs_windows,
            },
          });
        }
        resolve(ok(value));
      })();
    };

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      // 收**整棵树**：跑的是任意主机命令，`npm install` / dev server 派生的孙进程
      // 只杀 shell 就会留在用户机器上继续占端口占 CPU。不等 kill 完成、不等 close
      // ——超时立刻回话是本路径原有的好性质，杀树在后台自己走完。
      void killProcessTree(child);
      finishOk({
        timed_out: true,
        exit_code: null,
        stdout: truncateOut(stdout),
        stderr: truncateOut(stderr),
        cwd,
        note: `killed after ${timeoutSeconds}s`,
      });
    }, timeoutMs);

    child.stdout?.on("data", (chunk: Buffer | string) => {
      stdout += typeof chunk === "string" ? chunk : chunk.toString("utf8");
    });
    child.stderr?.on("data", (chunk: Buffer | string) => {
      stderr += typeof chunk === "string" ? chunk : chunk.toString("utf8");
    });
    // 杀树会把读到一半的管道扯断，那不是执行失败，别让它变成未捕获错误。
    child.stdout?.on("error", () => {});
    child.stderr?.on("error", () => {});
    child.on("error", (e) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(
        err(e.message || "host_shell spawn failed", "HostShellSpawnError"),
      );
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      finishOk({
        timed_out: false,
        exit_code: code ?? null,
        stdout: truncateOut(stdout),
        stderr: truncateOut(stderr),
        cwd,
      });
    });
  });
}
