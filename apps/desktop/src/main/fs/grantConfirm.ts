/**
 * Session-root write-grant confirm (organize / attach_rw).
 *
 * Read-only mounts stay silent. Write upgrades use a native system dialog
 * (same posture as execGate confirmDanger: cancel is default, Esc refuses).
 * Worker-triggered grants go through this IPC path — not CEO ask_user.
 */
import { BrowserWindow, dialog } from "electron";

export type SessionRootMode = "readonly" | "organize" | "attach_rw";

const RANK: Record<SessionRootMode, number> = {
  readonly: 0,
  organize: 1,
  attach_rw: 2,
};

/** True when ``have`` already authorizes ``need``. */
export function sessionModeCovers(
  have: SessionRootMode | undefined,
  need: SessionRootMode,
): boolean {
  return (have ? RANK[have] : -1) >= RANK[need];
}

function activeWindow(): BrowserWindow | null {
  return (
    BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0] ?? null
  );
}

/** Native write-grant dialog. Default / Esc = 取消. */
export async function confirmFolderWriteGrant(opts: {
  mode: Exclude<SessionRootMode, "readonly">;
  displayLabel: string;
}): Promise<boolean> {
  const organize = opts.mode === "organize";
  const win = activeWindow();
  const box = {
    type: "warning" as const,
    buttons: ["取消", organize ? "允许整理" : "允许改这个目录"],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
    title: "AgentCore",
    message: organize ? "允许整理该文件夹？" : "允许以可读写方式加入该文件夹？",
    detail: organize
      ? `${opts.displayLabel}\n本对话可将文件复制进去（不覆盖已有文件）。`
      : `${opts.displayLabel}\n本对话可改、可覆盖该文件夹里的文件。`,
  };
  const { response } = win
    ? await dialog.showMessageBox(win, box)
    : await dialog.showMessageBox(box);
  return response === 1;
}
