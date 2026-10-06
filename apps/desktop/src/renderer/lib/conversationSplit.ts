/**
 * 主列并排两场对话的纯状态。焦点栏跟路由走，左右位置不随焦点对调。
 * 持久化和「这一栏是哪场」的 React 范围不在这里。
 */

export const SPLIT_MIN_PANE_PX = 400;
export const SPLIT_DEFAULT_RATIO = 0.5;

/** `null` 是草稿栏。 */
export type PaneId = string | null;

export interface ConversationSplit {
  panes: [PaneId, PaneId];
  /** 焦点栏下标。路由上的对话就是这一栏。 */
  focus: 0 | 1;
  /** 左栏宽度占比。 */
  ratio: number;
}

export function splitRoomFits(mainWidthPx: number): boolean {
  return mainWidthPx >= SPLIT_MIN_PANE_PX * 2;
}

/** 两栏都还够打字时的占比。放不下时调用方不该再画分屏。 */
export function clampSplitRatio(ratio: number, widthPx: number): number {
  if (!Number.isFinite(ratio) || !Number.isFinite(widthPx) || widthPx <= 0) {
    return SPLIT_DEFAULT_RATIO;
  }
  const minFrac = SPLIT_MIN_PANE_PX / widthPx;
  if (minFrac >= 0.5) return SPLIT_DEFAULT_RATIO;
  return Math.min(1 - minFrac, Math.max(minFrac, ratio));
}

function normalizePane(value: unknown): PaneId | undefined {
  if (value === null) return null;
  if (typeof value === "string" && value.length > 0) return value;
  return undefined;
}

/** 读盘。坏数据、两栏都空，都当成没有分屏。 */
export function parseConversationSplit(raw: unknown): ConversationSplit | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;
  if (!Array.isArray(record.panes) || record.panes.length !== 2) return null;
  const left = normalizePane(record.panes[0]);
  const right = normalizePane(record.panes[1]);
  if (left === undefined || right === undefined) return null;
  if (left === null && right === null) return null;
  if (left !== null && left === right) return null;
  const focus: 0 | 1 = record.focus === 1 ? 1 : 0;
  const ratio =
    typeof record.ratio === "number"
      ? clampSplitRatio(record.ratio, SPLIT_MIN_PANE_PX * 4)
      : SPLIT_DEFAULT_RATIO;
  return { panes: [left, right], focus, ratio };
}

/**
 * 把 `target` 挂到焦点栏旁边。已经在某一栏里则只移焦点。
 * 和焦点栏是同一场时不开第二栏。
 */
export function openBeside(
  split: ConversationSplit | null,
  focusedId: PaneId,
  targetId: string,
): ConversationSplit | null {
  if (split) {
    const existing = split.panes.findIndex((pane) => pane === targetId);
    if (existing >= 0) {
      const focus = existing as 0 | 1;
      if (split.focus === focus) return split;
      return { ...split, focus };
    }
    const panes: [PaneId, PaneId] = [split.panes[0], split.panes[1]];
    panes[1 - split.focus] = targetId;
    if (panes[0] !== null && panes[0] === panes[1]) return split;
    return { ...split, panes };
  }
  if (focusedId === targetId) return null;
  return {
    panes: [focusedId, targetId],
    focus: 0,
    ratio: SPLIT_DEFAULT_RATIO,
  };
}

/** 侧栏单击 / 1–9 / 新建：只动焦点栏。目标已在另一栏则把焦点移过去。 */
export function replaceFocusedPane(
  split: ConversationSplit | null,
  routeId: PaneId,
): ConversationSplit | null {
  if (!split) return null;
  if (split.panes[split.focus] === routeId) return split;
  const existing = split.panes.findIndex((pane) => pane === routeId);
  if (existing >= 0) {
    return { ...split, focus: existing as 0 | 1 };
  }
  const panes: [PaneId, PaneId] = [split.panes[0], split.panes[1]];
  panes[split.focus] = routeId;
  if (panes[0] === panes[1]) return null;
  return { ...split, panes };
}

/** 草稿首发落成一场：占住原来的草稿栏，并成为焦点。 */
export function promoteDraftPane(
  split: ConversationSplit | null,
  conversationId: string,
): ConversationSplit | null {
  if (!split || !conversationId) return split;
  const index = split.panes.findIndex((pane) => pane === null);
  if (index < 0) return split;
  const panes: [PaneId, PaneId] = [split.panes[0], split.panes[1]];
  panes[index] = conversationId;
  if (panes[0] === panes[1]) return null;
  return { ...split, panes, focus: index as 0 | 1 };
}

export function closeSplitPane(split: ConversationSplit, index: 0 | 1): PaneId {
  return split.panes[1 - index];
}

/**
 * 列表已经落定之后，缺席的那场收掉。
 * `navigateTo: "stay"` 表示路由不用动。
 */
export function dropMissingPanes(
  split: ConversationSplit | null,
  exists: (id: string) => boolean,
): { split: ConversationSplit | null; navigateTo: PaneId | "stay" } {
  if (!split) return { split: null, navigateTo: "stay" };
  const alive = split.panes.map((pane) => pane === null || exists(pane));
  if (alive[0] && alive[1]) return { split, navigateTo: "stay" };
  if (!alive[0] && !alive[1]) return { split: null, navigateTo: null };
  const keepIndex = alive[0] ? 0 : 1;
  const keep = split.panes[keepIndex];
  const focusedDied = !alive[split.focus];
  return { split: null, navigateTo: focusedDied ? keep : "stay" };
}

/** 右键「在旁边打开」还指得着一场没在主列里的对话。 */
export function canOpenBeside(
  targetId: string,
  split: ConversationSplit | null,
  focusedId: PaneId,
  roomFits: boolean,
): boolean {
  if (!roomFits || !targetId) return false;
  if (split?.panes.includes(targetId)) return false;
  return focusedId !== targetId;
}

/** `#/` 和 `#/conversations/:id` 才把两栏画出来。回合详情不算。 */
export function isSplitHostPath(hash: string): boolean {
  const path = hash.replace(/^#/, "") || "/";
  if (path === "/") return true;
  return /^\/conversations\/[^/]+$/.test(path);
}

/** 并排且主列放得下时，两栏里的已落库对话都算在眼前。 */
export function besideIdsOnScene(
  hash: string,
  split: ConversationSplit | null,
  roomFits: boolean,
): string[] {
  if (!roomFits || !split || !isSplitHostPath(hash)) return [];
  const ids: string[] = [];
  for (const pane of split.panes) {
    if (pane) ids.push(pane);
  }
  return ids;
}
