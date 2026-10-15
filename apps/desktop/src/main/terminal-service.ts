/**
 * 桌面 Client Tools · 终端出口 —— bash 代码块「在终端运行」+ 工作区「在终端打开」。
 *
 * 用户直触 bash：renderer 聊天内 RunConfirm 后以 `rendererConfirmed: true` 调用，
 * 跳过 native OS 框（对标 Cursor 单一确认面）。未带确认的旧 string 入参仍走
 * {@link confirmBashRun} 兜底；「本会话都允许」与 grantSessionRun 共享 flag。
 * `openShellAtRoot` 仅 cd 到已授权工作区目录，不执行任意命令，故零确认（对标 VS Code）。
 */
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import {
  TERMINAL_CHANNELS,
  type TerminalRunResult,
} from "@shared/terminal-contract";
import { ipcMain } from "electron";
import { getStoredRoot } from "./fs-service";
import { confirmSessionRun, isSessionRunAllowed } from "./fs/execGate";
import { resolveLexical } from "./fs/pathGuard";

const PREVIEW_CAP = 2000;

function clip(s: string): string {
  return s.length > PREVIEW_CAP ? `${s.slice(0, PREVIEW_CAP)}\n…（已截断）` : s;
}

/** 主侧 native 确认兜底；与聊天 RunConfirm / grantSessionRun 共享本会话放行。 */
export function confirmBashRun(command: string): Promise<boolean> {
  return confirmSessionRun({
    message: "即将在本机终端运行以下命令",
    detail: clip(command) || "（空）",
    runLabel: "在终端运行",
  });
}

let wtAvailable: boolean | null = null;

/** Windows Terminal (`wt.exe`) when on PATH — cached after first probe. */
function isWindowsTerminalAvailable(): boolean {
  if (process.platform !== "win32") return false;
  if (wtAvailable !== null) return wtAvailable;
  try {
    const r = spawnSync("where.exe", ["wt"], {
      windowsHide: true,
      stdio: "ignore",
    });
    wtAvailable = r.status === 0;
  } catch {
    wtAvailable = false;
  }
  return wtAvailable;
}

function spawnWinDetached(
  file: string,
  args: string[],
): ReturnType<typeof spawn> {
  return spawn(file, args, {
    detached: true,
    stdio: "ignore",
    windowsHide: true,
  });
}

/** Spawn a new Windows shell tab/window running `command`, keeping the session open. */
function spawnWinRunCommand(command: string): ReturnType<typeof spawn> {
  if (isWindowsTerminalAvailable()) {
    return spawnWinDetached("wt.exe", [
      "new-tab",
      "--title",
      "GAgent",
      "powershell.exe",
      "-NoExit",
      "-NoLogo",
      "-Command",
      command,
    ]);
  }
  return spawnWinDetached("powershell.exe", [
    "-NoExit",
    "-NoLogo",
    "-Command",
    command,
  ]);
}

/** Open an interactive shell already cd'd to `absDir` on Windows. */
function spawnWinShellAtDirectory(absDir: string): ReturnType<typeof spawn> {
  if (isWindowsTerminalAvailable()) {
    return spawnWinDetached("wt.exe", ["-d", absDir]);
  }
  const ps = `Set-Location -LiteralPath ${JSON.stringify(absDir)}`;
  return spawnWinDetached("powershell.exe", [
    "-NoExit",
    "-NoLogo",
    "-Command",
    ps,
  ]);
}

/** 在用户默认 shell 的新终端窗口中执行命令（各平台 best-effort）。 */
export function spawnInUserTerminal(command: string): Promise<void> {
  const trimmed = command.trim();
  return new Promise((resolve, reject) => {
    let child: ChildProcess;
    if (process.platform === "win32") {
      child = spawnWinRunCommand(trimmed);
    } else if (process.platform === "darwin") {
      const script = [
        'tell application "Terminal"',
        "activate",
        `do script ${JSON.stringify(trimmed)}`,
        "end tell",
      ].join("\n");
      child = spawn("osascript", ["-e", script], {
        detached: true,
        stdio: "ignore",
      });
    } else {
      const shell = process.env.SHELL || "/bin/bash";
      child = spawn(
        "x-terminal-emulator",
        ["-e", shell, "-lc", `${trimmed}; exec ${shell} -i`],
        { detached: true, stdio: "ignore" },
      );
    }
    child.on("error", reject);
    child.on("spawn", () => {
      child.unref();
      resolve();
    });
  });
}

/** 在指定绝对目录打开交互式终端（仅 cd，不执行用户命令）。 */
export function spawnShellAtDirectory(absDir: string): Promise<void> {
  const dir = absDir.trim();
  if (!dir) return Promise.reject(new Error("目录为空"));
  if (process.platform === "win32") {
    return new Promise((resolve, reject) => {
      const child = spawnWinShellAtDirectory(dir);
      child.on("error", reject);
      child.on("spawn", () => {
        child.unref();
        resolve();
      });
    });
  }
  if (process.platform === "darwin") {
    return spawnInUserTerminal(`cd ${JSON.stringify(dir)} && clear`);
  }
  const shell = process.env.SHELL || "/bin/bash";
  return spawnInUserTerminal(`cd ${JSON.stringify(dir)} && exec ${shell} -i`);
}

export async function openShellAtWorkspace(
  rootId: string,
  subpath = "",
): Promise<TerminalRunResult> {
  const root = await getStoredRoot(rootId);
  if (!root) return { ok: false, reason: "本地目录未授权或已移除" };
  const rel = (subpath || ".").replace(/^\/+|\/+$/g, "") || ".";
  const abs = resolveLexical(root, rel === "" ? "." : rel);
  if (!abs) return { ok: false, reason: "工作区路径无效" };
  try {
    await spawnShellAtDirectory(abs);
    return { ok: true };
  } catch {
    return { ok: false, reason: "无法启动终端" };
  }
}

async function handleRunBash(input: unknown): Promise<TerminalRunResult> {
  let cmd = "";
  let rendererConfirmed = false;
  if (typeof input === "string") {
    cmd = input.trim();
  } else if (
    input != null &&
    typeof input === "object" &&
    "command" in input &&
    typeof (input as { command: unknown }).command === "string"
  ) {
    const obj = input as { command: string; rendererConfirmed?: unknown };
    cmd = obj.command.trim();
    rendererConfirmed = obj.rendererConfirmed === true;
  }
  if (!cmd) return { ok: false, reason: "命令为空" };
  // 聊天 RunConfirm 已确认，或本会话已放行 → 跳过 native；旧 string 入参仍走兜底。
  if (
    !rendererConfirmed &&
    !isSessionRunAllowed() &&
    !(await confirmBashRun(cmd))
  ) {
    return { ok: false, reason: "已取消" };
  }
  try {
    await spawnInUserTerminal(cmd);
    return { ok: true };
  } catch {
    return { ok: false, reason: "无法启动终端" };
  }
}

async function handleOpenShellAtRoot(
  rootId: unknown,
  subpath: unknown,
): Promise<TerminalRunResult> {
  if (typeof rootId !== "string" || !rootId.trim()) {
    return { ok: false, reason: "无效的本地根" };
  }
  const sub =
    typeof subpath === "string"
      ? subpath
      : subpath == null
        ? ""
        : String(subpath);
  return openShellAtWorkspace(rootId.trim(), sub);
}

/** 注册 terminal IPC；在 `app.whenReady` 内调用一次。 */
export function registerTerminalIpc(): void {
  ipcMain.handle(TERMINAL_CHANNELS.runBash, (_event, command: unknown) =>
    handleRunBash(command),
  );
  ipcMain.handle(
    TERMINAL_CHANNELS.openShellAtRoot,
    (_event, rootId: unknown, subpath: unknown) =>
      handleOpenShellAtRoot(rootId, subpath),
  );
}
