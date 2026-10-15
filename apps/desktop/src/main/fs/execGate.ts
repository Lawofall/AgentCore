/**
 * 危险原生出口的主进程侧确认门（IPC-001 / IPC-002 · 第五轮 IPC 权限面审计）。
 *
 * 门分工（对标 Cursor · 单一聊天确认面，2026-07）：
 * - **`workspaceOp('execute')`**：不再走本模块 native 框。`workspace_op_required` 仅在后端
 *   `ApprovalGate` 放行后触发 → 聊天审批卡是唯一人门（见 `fs/ipc.ts`）。
 * - **用户直触 bash**（代码块「在终端运行」）：聊天内 RunConfirm DecisionCard；经
 *   `rendererConfirmed` / {@link grantSessionRun} 跳过 native。未带确认的旧 IPC 入参仍走
 *   {@link confirmSessionRun} 兜底。
 * - **`openPath`**：仍白名单 + 单次 native 确认——黑名单永远列不全，且 Windows 会抹掉文件名
 *   末尾的点 / 空格使「假装无害」的名字仍被执行（红队 2026-06-30：E1/E2）。
 *
 * 本会话放行 flag（模块级，进程重启清零）：聊天「本会话都允许」经 {@link grantSessionRun}
 * IPC 置位；native bash 兜底三按钮亦可置位。不引入永久跨天 allowlist。
 *
 * 放在 IPC 缝（被 `fs/ipc.ts` / `terminal-service` 调用），不进 `opExecute`/`dispatch`
 * （后者纯函数、被 headless 单测直接调，混入 dialog 会破坏可测性）。
 * `requiresOpenConfirm` 为纯函数、单独可测。
 */
import { isSafeOpenExt } from "@shared/openable-ext";
import { BrowserWindow, dialog } from "electron";

/**
 * 本会话「允许运行」flag（bash native 兜底 + 聊天 RunConfirm「本会话都允许」共享）。
 * 进程重启清零。聊天路径经 {@link grantSessionRun} IPC 置位（本地可信用户取舍）。
 */
let sessionRunAllowed = false;

/** 本会话是否已放行运行类出口（bash）。 */
export function isSessionRunAllowed(): boolean {
  return sessionRunAllowed;
}

/**
 * 聊天内「本会话都允许」置位（`fs:grantSessionRun`）。幂等；进程重启清零。
 * 不引入永久跨天 allowlist。
 */
export function grantSessionRun(): void {
  sessionRunAllowed = true;
}

/** 测试用：清零本会话放行（生产路径靠进程生命周期自然清零）。 */
export function resetSessionRunAllowed(): void {
  sessionRunAllowed = false;
}

/** relPath 的最后一段文件名（容错两种路径分隔符）。 */
function baseName(relPath: string): string {
  return relPath.split(/[\\/]/).pop() ?? "";
}

/**
 * 用 OS 默认程序打开此路径前是否需要主侧确认（纯函数、可单测）。
 *
 * 判定表是 {@link isSafeOpenExt}（`shared/openable-ext.ts` 单一真相源，含 Windows 尾点 /
 * 空格规整、无扩展名即判不安全）。本地路径的策略是「名单外弹确认、确认后仍可开」——字节是
 * 用户自己放进盘里的，可信度足够；云端临时副本走 `fs/openTemp.ts` 的硬拒，无确认逃生口。
 */
export function requiresOpenConfirm(relPath: string): boolean {
  return !isSafeOpenExt(relPath);
}

function activeWindow(): BrowserWindow | null {
  return (
    BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0] ?? null
  );
}

/** 主侧 native 确认对话框；默认 / 取消按钮均为「取消」（安全失败）。返回用户是否放行。 */
async function confirmDanger(opts: {
  message: string;
  detail: string;
  confirmLabel: string;
}): Promise<boolean> {
  const win = activeWindow();
  const box = {
    type: "warning" as const,
    buttons: ["取消", opts.confirmLabel],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
    title: "GAgent 安全确认",
    message: opts.message,
    detail: opts.detail,
  };
  const { response } = win
    ? await dialog.showMessageBox(win, box)
    : await dialog.showMessageBox(box);
  return response === 1; // 仅「确认」按钮放行；关闭 / Esc → cancelId(0) → false
}

/**
 * 运行类三按钮确认（取消 | 单次运行 | 本会话都允许）。对标 Cursor session allow。
 * 默认 / 取消均为「取消」（安全失败）；仅点「本会话都允许」才置共享 flag。
 */
async function confirmRunWithSessionOption(opts: {
  message: string;
  detail: string;
  runLabel: string;
}): Promise<boolean> {
  if (sessionRunAllowed) return true;
  const win = activeWindow();
  const box = {
    type: "warning" as const,
    buttons: ["取消", opts.runLabel, "本会话都允许"],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
    title: "GAgent 安全确认",
    message: opts.message,
    detail: opts.detail,
  };
  const { response } = win
    ? await dialog.showMessageBox(win, box)
    : await dialog.showMessageBox(box);
  if (response === 2) {
    sessionRunAllowed = true;
    return true;
  }
  return response === 1;
}

// 预览上限：够看清意图，又不把对话框撑爆。code 与 stdin 各自独立截断。
const PREVIEW_CAP = 2000;

/** 把单段输入截断到预览上限（够看清意图，又不撑爆对话框）。 */
function clip(s: string): string {
  return s.length > PREVIEW_CAP ? `${s.slice(0, PREVIEW_CAP)}\n…（已截断）` : s;
}

/**
 * 历史 execute native 门（**非** `workspaceOp('execute')` 路径——该路径已改走聊天审批卡）。
 * 保留供单测覆盖三按钮 / stdin 预览 / 本会话 flag 语义；生产 IPC 不再调用。
 */
export function confirmExecute(
  args: Record<string, unknown>,
): Promise<boolean> {
  const language = String(args.language ?? "python");
  const cwd = String(args.cwd ?? "");
  const code = String(args.code ?? "");
  const stdin = args.stdin == null ? "" : String(args.stdin);
  const sections = [
    `工作目录：${cwd || "（绑定根）"}`,
    "",
    clip(code) || "（空）",
  ];
  if (stdin) sections.push("", "── 标准输入 stdin ──", clip(stdin));
  return confirmRunWithSessionOption({
    message: `即将在本机运行 ${language} 代码`,
    detail: sections.join("\n"),
    runLabel: "运行",
  });
}

/**
 * bash native 兜底门（未带 `rendererConfirmed` 的旧 IPC 入参）。
 * 与 {@link grantSessionRun} / 聊天 RunConfirm 共享本会话 flag。
 */
export function confirmSessionRun(opts: {
  message: string;
  detail: string;
  runLabel: string;
}): Promise<boolean> {
  return confirmRunWithSessionOption(opts);
}

/** openPath 门：打开「非已知安全类型」前确认（白名单姿态，见 {@link requiresOpenConfirm}）。 */
export function confirmOpenPath(relPath: string): Promise<boolean> {
  return confirmDanger({
    message: "即将用系统默认程序打开此文件",
    detail: `${baseName(relPath) || relPath}\n\n系统无法确认该类型是否安全——某些类型会在打开时执行代码。确认要继续打开吗？`,
    confirmLabel: "打开",
  });
}
