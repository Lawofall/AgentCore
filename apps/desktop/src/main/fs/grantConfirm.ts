/**
 * Session-root grant confirm (readonly / organize / attach_rw).
 *
 * Well-known Desktop/Downloads/Documents and already-sufficient session roots
 * stay silent. First mint of any other readonly abs path uses a native system
 * dialog (same posture as execGate: cancel is default, Esc refuses). Write
 * upgrades always confirm. Worker-triggered grants go through this IPC path —
 * not CEO ask_user.
 */
import { isAbsolute, relative, resolve, sep } from "node:path";
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

/** True when ``absPath`` is a well-known inbox root or a file/dir under one. */
export function absIsUnderAnyRoot(absPath: string, roots: string[]): boolean {
  const abs = resolve(absPath);
  for (const root of roots) {
    if (!root) continue;
    const r = resolve(root);
    const rel = relative(r, abs);
    if (rel === "") return true;
    if (!rel.startsWith("..") && !isAbsolute(rel)) return true;
    // Windows: relative("C:\\a", "C:\\a\\b") → "b"; outside → "..\\…"
    if (rel === ".." || rel.startsWith(`..${sep}`)) continue;
  }
  return false;
}

export function grantNeedsConfirm(opts: {
  mode: SessionRootMode;
  haveMode: SessionRootMode | undefined;
  wellKnown?: string | null;
  absUnderInbox: boolean;
}): boolean {
  if (sessionModeCovers(opts.haveMode, opts.mode)) return false;
  if (opts.mode === "readonly" && (opts.wellKnown || opts.absUnderInbox)) {
    return false;
  }
  return true;
}

function activeWindow(): BrowserWindow | null {
  return (
    BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0] ?? null
  );
}

/** Native readonly-grant dialog. Default / Esc = 取消. */
export async function confirmFolderReadGrant(opts: {
  displayLabel: string;
}): Promise<boolean> {
  const win = activeWindow();
  const box = {
    type: "question" as const,
    buttons: ["取消", "允许只读"],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
    title: "GAgent",
    message: "允许只读访问该文件夹？",
    detail: `${opts.displayLabel}\n本对话可读取其中的文件，不能改原件。仅本次对话。`,
  };
  const { response } = win
    ? await dialog.showMessageBox(win, box)
    : await dialog.showMessageBox(box);
  return response === 1;
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
    title: "GAgent",
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
