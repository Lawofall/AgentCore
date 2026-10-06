import type { PromptCatalogItem, PromptRail } from "@/lib/promptCatalog";
import { promptItemShelfCopy } from "@/lib/promptShelfTile";
import { buildAlwaysRows } from "@/lib/promptSizes";

/**
 * 工具箱「我的 / 市场」选区代数。
 *
 * 交互对齐文件树 / 桌面文件管理器：普通点击 = 单选并打开读卡，Ctrl/Cmd = 加减且不打开，
 * Shift = 从锚点连选，Esc = 清空。官方 HOW / 出厂工具不进这套选区。
 */

export interface MineSelectedItem {
  catalogId: string;
  mineId: string;
  label: string;
}

export interface MineSelection {
  items: readonly MineSelectedItem[];
  /** 最近一次非 Shift 点选的 catalogId；Shift 连选以它为起点。 */
  anchor: string | null;
}

export const EMPTY_MINE_SELECTION: MineSelection = { items: [], anchor: null };

export interface RowClickIntent {
  toggle: boolean;
  range: boolean;
}

export function clickIntent(e: {
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}): RowClickIntent {
  return { toggle: e.ctrlKey || e.metaKey, range: e.shiftKey };
}

export function isSelectionOnlyClick(intent: RowClickIntent): boolean {
  return intent.toggle || intent.range;
}

export function mineItemOf(item: PromptCatalogItem): MineSelectedItem | null {
  if (item.kind !== "mine" || !item.mineId) return null;
  return { catalogId: item.id, mineId: item.mineId, label: item.label };
}

export function selectionHas(sel: MineSelection, catalogId: string): boolean {
  return sel.items.some((i) => i.catalogId === catalogId);
}

export function selectionCatalogIds(sel: MineSelection): Set<string> {
  return new Set(sel.items.map((i) => i.catalogId));
}

function matchQuery(
  query: string,
  ...parts: Array<string | undefined>
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return parts.some((part) => part?.toLowerCase().includes(q));
}

function orderByVisible(
  items: readonly MineSelectedItem[],
  visible: readonly MineSelectedItem[],
): MineSelectedItem[] {
  const rest = new Map(items.map((i) => [i.catalogId, i]));
  const out: MineSelectedItem[] = [];
  for (const row of visible) {
    const hit = rest.get(row.catalogId);
    if (hit) {
      out.push(hit);
      rest.delete(row.catalogId);
    }
  }
  return [...out, ...rest.values()];
}

function pushMatchingMine(
  out: MineSelectedItem[],
  item: PromptCatalogItem,
  query: string,
  extra?: string,
): void {
  const mine = mineItemOf(item);
  if (!mine) return;
  const copy = promptItemShelfCopy(item);
  if (!matchQuery(query, extra, copy.title, copy.description, item.label)) {
    return;
  }
  out.push(mine);
}

/**
 * 与 PromptOverview 看得见的卡一致。
 * 根上：必带，再没归夹的按需；有搜索词时把夹里命中的卡也铺出来。
 * 点进夹且不在搜索：只算这只夹。夹名本身不把里面的卡算进可见名单。
 * Shift 连选和「可见」都以这份名单为准。
 */
export function flattenVisibleMineItems(
  rail: PromptRail,
  query = "",
  openFolderId: string | null = null,
): MineSelectedItem[] {
  const q = query.trim();
  if (!q && openFolderId) {
    const folder = rail.folders.find((row) => row.id === openFolderId);
    if (folder) {
      const inside: MineSelectedItem[] = [];
      for (const item of folder.items) pushMatchingMine(inside, item, q);
      return inside;
    }
  }
  const out: MineSelectedItem[] = [];
  for (const row of buildAlwaysRows(rail)) {
    const mine = mineItemOf(row.item);
    if (!mine) continue;
    const copy = promptItemShelfCopy(row.item, { alwaysChars: row.chars });
    if (!matchQuery(q, copy.title, copy.description, row.label)) continue;
    out.push(mine);
  }
  for (const folder of rail.folders) {
    if (folder.source !== "other" && !q) continue;
    for (const item of folder.items) {
      pushMatchingMine(out, item, q);
    }
  }
  return out;
}

export function selectRow(
  sel: MineSelection,
  item: MineSelectedItem,
  intent: RowClickIntent,
  visible: readonly MineSelectedItem[],
): MineSelection {
  if (intent.range && sel.anchor) {
    const from = visible.findIndex((r) => r.catalogId === sel.anchor);
    const to = visible.findIndex((r) => r.catalogId === item.catalogId);
    if (from >= 0 && to >= 0) {
      const [lo, hi] = from <= to ? [from, to] : [to, from];
      return {
        items: visible.slice(lo, hi + 1),
        anchor: sel.anchor,
      };
    }
  }
  if (intent.toggle) {
    const next = selectionHas(sel, item.catalogId)
      ? sel.items.filter((i) => i.catalogId !== item.catalogId)
      : [...sel.items, item];
    return { items: orderByVisible(next, visible), anchor: item.catalogId };
  }
  return { items: [item], anchor: item.catalogId };
}

/** 右键落在选区内保持整批；点在选区外收敛成单选。 */
export function selectionForContextMenu(
  sel: MineSelection,
  item: MineSelectedItem,
): MineSelection {
  if (selectionHas(sel, item.catalogId)) return sel;
  return { items: [item], anchor: item.catalogId };
}

export function dropFromSelection(
  sel: MineSelection,
  removedCatalogIds: readonly string[],
): MineSelection {
  if (removedCatalogIds.length === 0) return sel;
  const gone = new Set(removedCatalogIds);
  const items = sel.items.filter((i) => !gone.has(i.catalogId));
  if (items.length === sel.items.length) return sel;
  return {
    items,
    anchor: sel.anchor && !gone.has(sel.anchor) ? sel.anchor : null,
  };
}
