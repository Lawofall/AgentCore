import type { Conversation } from "@/stores/conversation";

/** Conversations shown inside an expanded group before「更多」. */
export const MAX_PER_GROUP = 5;
/** required 挤进后的组内上限（含回塞的帽外 required）。 */
export const MAX_GROUP_VISIBLE = 6;

export const BARE_LIMIT_SOLO = 15;
export const BARE_LIMIT_WITH_GROUPS = 10;

function isArchived(c: Conversation): boolean {
  return c.archived === true;
}

/**
 * 组内可见行：无 required / required 已在 Top 5 仍 Top 5；帽外 required 挤进，
 * 优先留住它们再按原 recency 填满，总数 ≤6。不把归档行拉回来。
 */
export function pickGroupVisible(
  unpinned: readonly Conversation[],
  requiredIds: ReadonlySet<string>,
): Conversation[] {
  const live = unpinned.filter((c) => !isArchived(c));
  const top = live.slice(0, MAX_PER_GROUP);
  const required = live.filter((c) => requiredIds.has(c.id));
  if (required.length === 0) return top;
  const topIds = new Set(top.map((c) => c.id));
  const overflow = required.filter((c) => !topIds.has(c.id));
  if (overflow.length === 0) return top;
  const cap = MAX_GROUP_VISIBLE;
  const takeRequired = required.slice(0, cap);
  const taken = new Set(takeRequired.map((c) => c.id));
  const others = live
    .filter((c) => !taken.has(c.id))
    .slice(0, cap - takeRequired.length);
  const keep = new Set([...takeRequired, ...others].map((c) => c.id));
  return live.filter((c) => keep.has(c.id));
}

/**
 * 裸聊可见行：先 10/15 帽，再把 currentId 与 required 像原来的 currentId 一样回塞。
 * 只从已有裸聊里找——归档 / 文件夹对话不会被拉进这一区。
 */
export function pickBareVisible(
  bare: readonly Conversation[],
  opts: {
    limit: number;
    currentId: string | null;
    requiredIds: ReadonlySet<string>;
  },
): Conversation[] {
  const live = bare.filter((c) => !isArchived(c));
  const top = live.slice(0, opts.limit);
  const keep = new Set(top.map((c) => c.id));
  const extras: Conversation[] = [];
  const want = new Set<string>();
  if (opts.currentId) want.add(opts.currentId);
  for (const id of opts.requiredIds) want.add(id);
  for (const id of want) {
    if (keep.has(id)) continue;
    const found = live.find((c) => c.id === id);
    if (!found) continue; // 归档或不在裸聊区：不拉回
    extras.push(found);
    keep.add(id);
  }
  return extras.length === 0 ? top : [...top, ...extras];
}

/**
 * 折组：required 期间计算覆盖 persist（不写回）；required 消失后回到 persist /
 * 当前对话所在组的默认。
 */
export function isGroupExpanded(opts: {
  stored: boolean | undefined;
  isActiveFolder: boolean;
  hasRequired: boolean;
}): boolean {
  if (opts.hasRequired) return true;
  return opts.stored !== undefined ? opts.stored : opts.isActiveFolder;
}

/** Ctrl/Cmd+1…9 对应侧栏从上到下当前看得见的对话行（不含组头 /「更多」/「查看全部」）。 */
export const RAIL_HOTKEY_SLOTS = 9;

function byRecency(a: Conversation, b: Conversation): number {
  return (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0);
}

/** Folder group as the rail renders it — only `folder.id` + member chats are needed. */
export interface RailVisibilityGroup {
  folder: { id: string };
  convs: readonly Conversation[];
}

/**
 * 侧栏对话行的视觉序：置顶 → 已展开的自有组 → 已展开的「与我共享」组 → 裸聊。
 * 折叠组、组头、「更多」、归档回拉都不进列——与侧栏三区看见的行同一套。
 */
export function listVisibleRailConversations(opts: {
  conversations: readonly Conversation[];
  ownedGroups: readonly RailVisibilityGroup[];
  sharedGroups: readonly RailVisibilityGroup[];
  expandedSections: Record<string, boolean | undefined>;
  currentId: string | null;
  requiredIds: ReadonlySet<string>;
}): Conversation[] {
  const {
    conversations,
    ownedGroups,
    sharedGroups,
    expandedSections,
    currentId,
    requiredIds,
  } = opts;

  const out: Conversation[] = [
    ...conversations.filter((c) => c.pinned).sort(byRecency),
  ];

  const active = conversations.find((c) => c.id === currentId);
  const activeFolderId =
    active && !active.pinned ? (active.folderId ?? null) : null;

  const appendExpandedGroupRows = (groups: readonly RailVisibilityGroup[]) => {
    for (const { folder, convs } of groups) {
      const visible = convs.filter((c) => !c.pinned);
      const expanded = isGroupExpanded({
        stored: expandedSections[folder.id],
        isActiveFolder: folder.id === activeFolderId,
        hasRequired: visible.some((c) => requiredIds.has(c.id)),
      });
      if (!expanded) continue;
      out.push(...pickGroupVisible(visible, requiredIds));
    }
  };

  appendExpandedGroupRows(ownedGroups);
  appendExpandedGroupRows(sharedGroups);

  const hasGroups = ownedGroups.length > 0 || sharedGroups.length > 0;
  const bare = conversations
    .filter((c) => !c.folderId && !c.pinned)
    .sort(byRecency);
  out.push(
    ...pickBareVisible(bare, {
      limit: hasGroups ? BARE_LIMIT_WITH_GROUPS : BARE_LIMIT_SOLO,
      currentId,
      requiredIds,
    }),
  );
  return out;
}

/** First {@link RAIL_HOTKEY_SLOTS} visible rows → 1-based index by conversation id. */
export function railHotkeySlots(
  visible: readonly Conversation[],
): Map<string, number> {
  const map = new Map<string, number>();
  const cap = Math.min(visible.length, RAIL_HOTKEY_SLOTS);
  for (let i = 0; i < cap; i++) {
    const row = visible[i];
    if (row) map.set(row.id, i + 1);
  }
  return map;
}

/** `digit` is `"1"`…`"9"`. Missing / out of range → `undefined` (caller must not steal the chord). */
export function conversationAtRailHotkey(
  digit: string,
  visible: readonly Conversation[],
): Conversation | undefined {
  if (!/^[1-9]$/.test(digit)) return undefined;
  return visible[Number(digit) - 1];
}
