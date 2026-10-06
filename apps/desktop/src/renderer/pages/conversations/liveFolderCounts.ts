import { canonicalFolderIds } from "@/hooks/useWorkspaceGroups";
import type { FolderMeta } from "@/services/folders";
import { ALL_KEY, isRealFolderFilter } from "./constants";

/** One live chat, only the fields the folder rail counts. */
export interface LiveChatFolderRef {
  folderId?: string | null;
}

export interface LiveFolderCounts {
  /** Chats with no folder, or a folder id missing from the catalog (快速对话). */
  ungrouped: number;
  /** Displayed folder id → live chats, after local-binding rollup. */
  perFolder: Map<string, number>;
  /** Every catalog id → the displayed row. See {@link canonicalFolderIds}. */
  canonical: Map<string, string>;
}

/**
 * Count live chats per displayed folder.
 *
 * Callers pass the live list (archived and deleted stay out). Pinned chats
 * count. Historical duplicate local bindings roll onto the oldest row, the
 * same partition the sidebar groups use — otherwise a row can show 0 while
 * its chats still exist under a hidden sibling id.
 */
export function partitionLiveConversationCounts(
  conversations: readonly LiveChatFolderRef[],
  folders: readonly FolderMeta[],
): LiveFolderCounts {
  const canonical = canonicalFolderIds(folders);
  const perFolder = new Map<string, number>();
  let ungrouped = 0;
  for (const c of conversations) {
    const fid = c.folderId;
    if (!fid || !canonical.has(fid)) {
      ungrouped += 1;
      continue;
    }
    const shown = canonical.get(fid) ?? fid;
    perFolder.set(shown, (perFolder.get(shown) ?? 0) + 1);
  }
  return { ungrouped, perFolder, canonical };
}

/** A live chat sits in「快速对话」when it has no displayed folder. */
export function isUngroupedLiveChat(
  folderId: string | null | undefined,
  canonical: ReadonlyMap<string, string>,
): boolean {
  return !folderId || !canonical.has(folderId);
}

/** Folder filter includes chats that roll up to the same displayed row. */
export function liveChatInFolder(
  folderId: string | null | undefined,
  selectedFolderId: string,
  canonical: ReadonlyMap<string, string>,
): boolean {
  if (!folderId) return false;
  const owner = canonical.get(folderId);
  if (!owner) return false;
  const selected = canonical.get(selectedFolderId) ?? selectedFolderId;
  return owner === selected;
}

/** Keep catalog order. A displayed row stays only while its badge is at least 1. */
export function foldersWithLiveChats<T extends { id: string }>(
  folders: readonly T[],
  perFolder: ReadonlyMap<string, number>,
): T[] {
  return folders.filter((f) => (perFolder.get(f.id) ?? 0) > 0);
}

/**
 * Once the catalog has loaded, a folder filter with no live chats leaves
 * for「全部对话」. A selection sitting on a hidden duplicate binding moves
 * to the displayed row. `null` means keep the current selection.
 */
export function resolveFolderFilterSelection(
  selected: string,
  folderIds: Set<string>,
  perFolder: ReadonlyMap<string, number>,
  canonical: ReadonlyMap<string, string> | undefined,
): string | null {
  if (!isRealFolderFilter(selected, folderIds)) return null;
  const shown = canonical?.get(selected) ?? selected;
  if ((perFolder.get(shown) ?? 0) === 0) return ALL_KEY;
  if (shown !== selected) return shown;
  return null;
}

/**
 * Cmd+K folder hit with no live chat opens the files page.
 * Before the grouped cache has landed, stay on the conversation page —
 * an empty cache is not evidence the folder has no chats.
 */
export function folderHitHasNoLiveChat(
  folderId: string,
  conversations: readonly LiveChatFolderRef[],
  folders: readonly FolderMeta[],
  groupedSettled: boolean,
): boolean {
  if (!groupedSettled) return false;
  const { perFolder, canonical } = partitionLiveConversationCounts(
    conversations,
    folders,
  );
  const shown = canonical.get(folderId) ?? folderId;
  return (perFolder.get(shown) ?? 0) === 0;
}
