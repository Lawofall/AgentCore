/**
 * 真 OS 浮窗生命周期（方案 C）—— BrowserWindow + parent=主窗（JetBrains Float），
 * 复用主窗 preload / defaultSession；不提供最小化。
 *
 * 不改 Local Browser 附着模型；不做无 preload 的 deprecated preview 子窗。
 */

import {
  FLOAT_WINDOW_CHANNELS,
  FLOAT_WINDOW_MAX,
  type FloatWindowBounds,
  type FloatWindowCloseReason,
  type FloatWindowClosedPayload,
  type FloatWindowOpenInput,
} from "@shared/float-window-contract";
import { isSafeExternalUrl } from "@shared/safe-url";
import { BrowserWindow, ipcMain, shell } from "electron";
import { isRecord, requireStringFields } from "./ipc-validate";
import {
  broadcastShellPresence,
  watchWindowForShellPresence,
} from "./shell-presence";

const DEFAULT_WIDTH = 640;
const DEFAULT_HEIGHT = 800;
const MIN_WIDTH = 320;
const MIN_HEIGHT = 240;
/** 无 bounds 时相对主窗居中后再偏一点（对齐 VS Code aux cascade）。 */
const DEFAULT_OFFSET = 48;
/**
 * 与主进程 `app.setAppUserModelId` 同源（= 安装器 appId）——任务栏单图标分组 + 系统通知可送达。
 * 否决每真窗独立 AppUserModelID；否决为 toast 另留一套 AUMID。构建期按通道注入。
 */
declare const __WINDOWS_APP_USER_MODEL_ID__: string | undefined;
const WINDOWS_APP_USER_MODEL_ID =
  typeof __WINDOWS_APP_USER_MODEL_ID__ !== "undefined" &&
  __WINDOWS_APP_USER_MODEL_ID__
    ? __WINDOWS_APP_USER_MODEL_ID__
    : "xyz.fashitianxia.gagent";

export type FloatWindowDeps = {
  /** 主窗（收 closed 事件）；可暂为空。 */
  getMainWindow: () => BrowserWindow | null;
  /** 与主窗同 origin 的 loadURL（含 `#/float?cid=&tab=`）。 */
  buildFloatUrl: (conversationId: string, tabId: string) => string;
  /** 导航白名单基址（prod: app://agentcore；dev: Vite URL）。 */
  allowedNavigationBase: string;
  preloadPath: string;
  icon?: string;
};

type FloatEntry = {
  win: BrowserWindow;
  conversationId: string;
};

const floats = new Map<string, FloatEntry>();
/** tabId → 主动关窗原因；缺省则用户点关闭 ≈ dock（reason=user）。 */
const pendingReasons = new Map<string, FloatWindowCloseReason>();

let deps: FloatWindowDeps | null = null;
let ipcRegistered = false;
/** 应用退出中：关窗不向主窗发 closed（主窗可能已毁）。 */
let silencingClosed = false;

function notifyClosed(tabId: string, reason: FloatWindowCloseReason): void {
  if (silencingClosed) return;
  const main = deps?.getMainWindow() ?? null;
  if (!main || main.isDestroyed()) return;
  const payload: FloatWindowClosedPayload = { tabId, reason };
  main.webContents.send(FLOAT_WINDOW_CHANNELS.closed, payload);
}

function normalizeBounds(raw: unknown): FloatWindowBounds | null {
  if (!isRecord(raw)) return null;
  const x = raw.x;
  const y = raw.y;
  const width = raw.width;
  const height = raw.height;
  if (
    typeof x !== "number" ||
    typeof y !== "number" ||
    typeof width !== "number" ||
    typeof height !== "number" ||
    !Number.isFinite(x) ||
    !Number.isFinite(y) ||
    !Number.isFinite(width) ||
    !Number.isFinite(height)
  ) {
    return null;
  }
  return {
    x: Math.round(x),
    y: Math.round(y),
    width: Math.max(MIN_WIDTH, Math.round(width)),
    height: Math.max(MIN_HEIGHT, Math.round(height)),
  };
}

function parseOpenInput(raw: unknown): FloatWindowOpenInput | null {
  const required = requireStringFields(raw, [
    "tabId",
    "conversationId",
    "title",
  ]);
  if (!required) return null;
  const tabId = required.tabId.trim();
  const conversationId = required.conversationId.trim();
  const title = required.title.trim();
  if (!tabId || !conversationId || !title) return null;
  const bounds =
    isRecord(raw) && raw.bounds !== undefined
      ? normalizeBounds(raw.bounds)
      : undefined;
  if (isRecord(raw) && raw.bounds !== undefined && !bounds) return null;
  return {
    tabId,
    conversationId,
    title,
    ...(bounds ? { bounds } : {}),
  };
}

function parseTabId(raw: unknown): string | null {
  const fields = requireStringFields(raw, ["tabId"]);
  if (!fields) return null;
  const tabId = fields.tabId.trim();
  return tabId || null;
}

/**
 * 无显式 bounds：相对主窗客户区居中，再按已有真窗数 cascade 错开
 *（第 N 个偏移 N×DEFAULT_OFFSET，对齐 VS Code aux；禁止同点叠死）。
 * 主窗不可用则省略 x/y（交给 OS 居中）。禁止死钉 (0,0)。
 */
function resolveOpenBounds(
  inputBounds: FloatWindowBounds | undefined,
  main: BrowserWindow | null,
  cascadeIndex: number,
): { width: number; height: number; x?: number; y?: number } {
  if (inputBounds) {
    return {
      x: inputBounds.x,
      y: inputBounds.y,
      width: inputBounds.width,
      height: inputBounds.height,
    };
  }
  const width = DEFAULT_WIDTH;
  const height = DEFAULT_HEIGHT;
  if (main && !main.isDestroyed()) {
    const mb = main.getBounds();
    const offset = DEFAULT_OFFSET * (Math.max(0, cascadeIndex) + 1);
    return {
      x: Math.round(mb.x + (mb.width - width) / 2) + offset,
      y: Math.round(mb.y + (mb.height - height) / 2) + offset,
      width,
      height,
    };
  }
  return { width, height };
}

function lockFloatNavigation(win: BrowserWindow): void {
  const base = deps?.allowedNavigationBase ?? "";
  win.webContents.setWindowOpenHandler((details) => {
    if (isSafeExternalUrl(details.url)) {
      void shell.openExternal(details.url);
    } else {
      console.warn(
        `[security] blocked openExternal for unsafe URL scheme: ${details.url}`,
      );
    }
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!base || !url.startsWith(base)) {
      event.preventDefault();
      console.warn(`[security] blocked float-window navigation to: ${url}`);
    }
  });
}

function removeEntry(tabId: string, win: BrowserWindow): void {
  const entry = floats.get(tabId);
  if (entry?.win === win) {
    floats.delete(tabId);
  }
  const reason = pendingReasons.get(tabId) ?? "user";
  pendingReasons.delete(tabId);
  notifyClosed(tabId, reason);
}

function closeByTabId(
  tabId: string,
  reason: Exclude<FloatWindowCloseReason, "user">,
): void {
  const entry = floats.get(tabId);
  if (!entry || entry.win.isDestroyed()) {
    floats.delete(tabId);
    return;
  }
  pendingReasons.set(tabId, reason);
  entry.win.close();
}

/**
 * 打开或聚焦同 tabId 真窗。满 {@link FLOAT_WINDOW_MAX} 且非复用 → false。
 */
export function openFloatWindow(input: FloatWindowOpenInput): boolean {
  if (!deps) return false;
  const tabId = input.tabId.trim();
  const conversationId = input.conversationId.trim();
  const title = input.title.trim();
  if (!tabId || !conversationId || !title) return false;

  const existing = floats.get(tabId);
  if (existing && !existing.win.isDestroyed()) {
    if (existing.win.isMinimized()) existing.win.restore();
    if (!existing.win.isVisible()) existing.win.show();
    existing.win.setTitle(title);
    // Skip focus when already focused — avoids re-emitting window `focus`
    // which the float renderer broadcasts back as focusFloat.
    if (!existing.win.isFocused()) existing.win.focus();
    return true;
  }
  if (existing) floats.delete(tabId);

  if (floats.size >= FLOAT_WINDOW_MAX) return false;

  const main = deps.getMainWindow();
  // Existing count = cascade index for the window about to be created.
  const bounds = resolveOpenBounds(input.bounds, main, floats.size);

  const win = new BrowserWindow({
    width: bounds.width,
    height: bounds.height,
    ...(bounds.x !== undefined && bounds.y !== undefined
      ? { x: bounds.x, y: bounds.y }
      : {}),
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    title,
    show: false,
    frame: false,
    // JetBrains Float：owner=主窗 → OS 相对主窗置顶（开第二窗不闪沉）。
    // 禁止 modal / type:"toolbar"|tool；minimizable:false — 真窗否决最小化。
    ...(main && !main.isDestroyed() ? { parent: main } : {}),
    minimizable: false,
    skipTaskbar: false,
    autoHideMenuBar: true,
    ...(deps.icon ? { icon: deps.icon } : {}),
    ...(process.platform === "darwin" && {
      titleBarStyle: "hidden" as const,
      trafficLightPosition: { x: 12, y: 12 },
    }),
    webPreferences: {
      preload: deps.preloadPath,
      // 复用 defaultSession（与主窗同登录 cookie）；禁止 preview 式无 preload 隔离子窗。
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Windows：与主进程同一 appId，保证单图标分组 + 多窗预览（title = 缩略图标题）。
  if (process.platform === "win32") {
    win.setAppDetails({ appId: WINDOWS_APP_USER_MODEL_ID });
  }

  floats.set(tabId, { win, conversationId });
  win.on("closed", () => removeEntry(tabId, win));
  lockFloatNavigation(win);
  watchWindowForShellPresence(win);
  broadcastShellPresence();

  win.once("ready-to-show", () => {
    if (!win.isDestroyed()) win.show();
  });

  void win.loadURL(deps.buildFloatUrl(conversationId, tabId));
  return true;
}

export function dockFloatWindow(tabId: string): void {
  closeByTabId(tabId, "dock");
}

export function destroyFloatWindow(tabId: string): void {
  closeByTabId(tabId, "destroy");
}

/** 主应用退出 / 主窗关闭时收尽真窗（不向主窗发 closed）。 */
export function destroyAllFloatWindows(): void {
  silencingClosed = true;
  try {
    for (const [tabId, entry] of [...floats.entries()]) {
      pendingReasons.delete(tabId);
      if (!entry.win.isDestroyed()) {
        entry.win.removeAllListeners("closed");
        entry.win.destroy();
      }
    }
    floats.clear();
    pendingReasons.clear();
  } finally {
    silencingClosed = false;
  }
}

export function floatWindowCount(): number {
  return floats.size;
}

export function hasFloatWindow(tabId: string): boolean {
  const entry = floats.get(tabId);
  return Boolean(entry && !entry.win.isDestroyed());
}

/** Live OS floats for shell-presence / scene (conversation id at open). */
export function listFloatEntries(): ReadonlyArray<{
  win: BrowserWindow;
  conversationId: string;
}> {
  return [...floats.values()].filter((e) => !e.win.isDestroyed());
}

/** True if `win` is a managed方案 C float (for chrome IPC routing). */
export function isManagedFloatWindow(win: BrowserWindow): boolean {
  for (const entry of floats.values()) {
    if (entry.win === win && !win.isDestroyed()) return true;
  }
  return false;
}

/**
 * 真窗否决最小化（无按钮 + minimizable:false + IPC no-op）。
 * 主窗仍走系统 minimize。
 */
export function minimizeBrowserWindow(win: BrowserWindow): void {
  if (win.isDestroyed()) return;
  if (isManagedFloatWindow(win)) return;
  win.minimize();
}

/** 单测重置。 */
export function resetFloatWindowsForTests(): void {
  silencingClosed = true;
  try {
    for (const entry of floats.values()) {
      if (!entry.win.isDestroyed()) {
        entry.win.removeAllListeners("closed");
        entry.win.destroy();
      }
    }
  } finally {
    floats.clear();
    pendingReasons.clear();
    silencingClosed = false;
    deps = null;
    ipcRegistered = false;
  }
}

export function configureFloatWindows(next: FloatWindowDeps): void {
  deps = next;
}

export function registerFloatWindowIpc(next: FloatWindowDeps): void {
  deps = next;
  if (ipcRegistered) return;
  ipcRegistered = true;

  ipcMain.handle(FLOAT_WINDOW_CHANNELS.open, (_e, raw: unknown): boolean => {
    const input = parseOpenInput(raw);
    if (!input) return false;
    return openFloatWindow(input);
  });

  ipcMain.handle(FLOAT_WINDOW_CHANNELS.dock, (_e, raw: unknown): void => {
    const tabId = parseTabId(raw);
    if (tabId) dockFloatWindow(tabId);
  });

  ipcMain.handle(FLOAT_WINDOW_CHANNELS.destroy, (_e, raw: unknown): void => {
    const tabId = parseTabId(raw);
    if (tabId) destroyFloatWindow(tabId);
  });
}

/**
 * 构建真窗 hash 路由（与壳块约定一致）。
 * 例：`#/float?cid=abc&tab=run%3A1`
 */
export function buildFloatHashRoute(
  conversationId: string,
  tabId: string,
): string {
  const q = new URLSearchParams({
    cid: conversationId,
    tab: tabId,
  });
  return `#/float?${q.toString()}`;
}
