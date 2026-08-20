import type { DeletedConversationMeta } from "@/services/conversations";
import type { DeletedFolderMeta } from "@/services/folders";
import type { Conversation } from "@/stores/conversation";
import { UNGROUPED_KEY } from "@/stores/folders";

/** Synthetic left-pane filter key for「全部对话」(not a real folder). */
export const ALL_KEY = "__all__";
/** Synthetic left-pane filter key for the「已归档」view (归档对话). */
export const ARCHIVED_KEY = "__archived__";
/** Synthetic left-pane filter key for the「最近删除」view (已删对话 + 已删文件夹). */
export const TRASH_KEY = "__trash__";

/** Stable empty list so the archived view keeps a constant reference until data. */
export const EMPTY_CONVERSATIONS: Conversation[] = [];
/** Stable empty list so the trash view keeps a constant reference until data. */
export const EMPTY_DELETED_FOLDERS: DeletedFolderMeta[] = [];
/** Same, for the deleted-conversations half of「最近删除」. */
export const EMPTY_DELETED_CONVERSATIONS: DeletedConversationMeta[] = [];

/**
 * How long a deleted project or conversation still has, phrased from the server's
 * `purge_at`. That timestamp is the *earliest* the sweeper may purge (it runs on a
 * cadence), so the day count floors rather than rounds — never promise more time
 * than the server committed to.
 */
export function retentionRemainingLabel(
  purgeAt: string,
  now: number = Date.now(),
): string {
  const left = Date.parse(purgeAt) - now;
  if (Number.isNaN(left)) return "";
  if (left <= 0) return "即将清理";
  const days = Math.floor(left / 86_400_000);
  return days < 1 ? "剩不到 1 天" : `剩 ${days} 天`;
}

/** Days without activity for the「久未活跃」quick filter on the management page. */
export const STALE_DAYS = 30;

export function byPinnedThenRecency(a: Conversation, b: Conversation): number {
  if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
  return (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0);
}

export function activeFilterName(
  selected: string,
  folders: { id: string; name: string }[],
): string {
  if (selected === ALL_KEY) return "全部对话";
  if (selected === UNGROUPED_KEY) return "未分组";
  if (selected === ARCHIVED_KEY) return "已归档";
  if (selected === TRASH_KEY) return "最近删除";
  return folders.find((f) => f.id === selected)?.name ?? "全部对话";
}

/** The synthetic view keys — anything else is a real folder id. */
export function isSyntheticFilter(selected: string): boolean {
  return (
    selected === ALL_KEY ||
    selected === UNGROUPED_KEY ||
    selected === ARCHIVED_KEY ||
    selected === TRASH_KEY
  );
}

export function isRealFolderFilter(
  selected: string,
  folderIds: Set<string>,
): boolean {
  return !isSyntheticFilter(selected) && folderIds.has(selected);
}

/** Navigation options for `/files` when jumping from a folder context. */
export function filesFocusState(
  folderId?: string | null,
): { state: { focusWsId: string } } | undefined {
  if (!folderId) return undefined;
  return { state: { focusWsId: `folder:${folderId}` } };
}

export function newChatFolderTarget(
  selected: string,
  folderIds: Set<string>,
): string | null {
  if (
    selected !== ALL_KEY &&
    selected !== UNGROUPED_KEY &&
    folderIds.has(selected)
  ) {
    return selected;
  }
  return null;
}
