import type { FileSortBy } from "@/components/files/fileTreeTypes";
import { uiGet, uiSet } from "@/lib/uiStorage";

/** `ws_id = conv:<conversationId>` → its conversation id (scratch workspace). */
export function conversationIdOf(wsId: string): string | null {
  return wsId.startsWith("conv:") ? wsId.slice("conv:".length) : null;
}

/** `ws_id = folder:<folderId>` → its folder id (project shared workspace). */
export function folderIdOf(wsId: string): string | null {
  return wsId.startsWith("folder:") ? wsId.slice("folder:".length) : null;
}

const RAIL_KEY = "files-rail-width";
const RAIL_MIN = 200;
const RAIL_MAX = 600;
const RAIL_DEFAULT = 288; // = Tailwind w-72，沿用旧固定宽度作默认

export function clampRail(px: number): number {
  return Math.min(RAIL_MAX, Math.max(RAIL_MIN, Math.round(px)));
}

export function loadRailWidth(): number {
  const raw = uiGet<number>(RAIL_KEY);
  return typeof raw === "number" && Number.isFinite(raw)
    ? clampRail(raw)
    : RAIL_DEFAULT;
}

export function saveRailWidth(px: number): void {
  uiSet(RAIL_KEY, clampRail(px));
}

// Generic Set<string> persistence — every rail fold state (工作区段 / 设定段 /
// 主题子夹) is "a set of ids in their non-default state". Tolerates unavailable / corrupt storage.
function loadStringSet(key: string): Set<string> {
  const parsed = uiGet<unknown>(key);
  if (!Array.isArray(parsed)) return new Set();
  return new Set(parsed.filter((p): p is string => typeof p === "string"));
}

function saveStringSet(key: string, set: Set<string>): void {
  uiSet(key, [...set]);
}

// 工作区段默认折叠（只露根标题），展开过的记进这个 set 持久化，下次进页面沿用
// （与 FileTree 内部 per-source 目录折叠态各管一层：这一层管「整个工作区段是否展开」）。
const WS_EXPANDED_KEY = "files-ws-expanded";

export function loadExpandedWs(): Set<string> {
  return loadStringSet(WS_EXPANDED_KEY);
}

export function saveExpandedWs(set: Set<string>): void {
  saveStringSet(WS_EXPANDED_KEY, set);
}

// 文件夹条目不再套约定目录。旧展开键只留给已经写在本地的折叠状态。
const AGENTCORE_COLLAPSED_KEY = "files-agentcore-collapsed";
const AGENTCORE_EXPANDED_KEY = "files-agentcore-expanded";

export function loadAgentCoreCollapsed(): Set<string> {
  return loadStringSet(AGENTCORE_COLLAPSED_KEY);
}

export function saveAgentCoreCollapsed(set: Set<string>): void {
  saveStringSet(AGENTCORE_COLLAPSED_KEY, set);
}

export function loadAgentCoreExpanded(): Set<string> {
  return loadStringSet(AGENTCORE_EXPANDED_KEY);
}

export function saveAgentCoreExpanded(set: Set<string>): void {
  saveStringSet(AGENTCORE_EXPANDED_KEY, set);
}

// 树的排序依据：**偏好**（跨会话保留），与筛选框那种瞬态搜索相对。整个中枢一个值，
// 不按工作区分开——用户想的是「我要按时间看文件」，不是「这个夹按时间那个夹按名字」。
const SORT_KEY = "files-sort-by";

export function loadFileSort(): FileSortBy {
  const raw = uiGet<unknown>(SORT_KEY);
  // 旧偏好 `"size"` 已从 FileSortBy 拿掉，回落到名称，不能再让 size 进类型。
  return raw === "mtime" ? raw : "name";
}

export function saveFileSort(by: FileSortBy): void {
  uiSet(SORT_KEY, by);
}

export interface Tab {
  wsId: string;
  path: string;
  name: string;
  dirty?: boolean;
  confirmDiscard?: boolean;
}

/** Stable per-file key (a workspace's path is unique within it). */
export function tabKey(wsId: string, path: string): string {
  return `${wsId}:${path}`;
}

/**
 * Synthetic tab paths for a workspace's **non-file** panels (版本 / 软删区). They ride
 * the real ws id — unlike the rules synthetic workspace — so a workspace
 * that disappears takes its panels' tabs with it, and so 打开 goes through the same
 * `onOpenFile(path, name)` seam every rail row already has. The double-underscore
 * shape keeps them from colliding with a real workspace-relative path.
 */
export const WS_VERSIONS_PATH = "__ws_versions__";
export const WS_TRASH_PATH = "__ws_trash__";

/** Tab label for a workspace panel — the panel kind plus which workspace it is. */
export function workspacePanelTabName(path: string, wsName: string): string {
  return path === WS_VERSIONS_PATH ? `版本 · ${wsName}` : `软删区 · ${wsName}`;
}
