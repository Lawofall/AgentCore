import {
  useArchivedConversations,
  useConversationTrash,
  useConversations,
  useGroupedConversationsSettled,
} from "@/hooks/useConversations";
import { useFolderTrash, useFolders } from "@/hooks/useFolders";
import { canonicalFolderIds } from "@/hooks/useWorkspaceGroups";
import {
  type FolderMeta,
  dedupeFoldersByLocalBinding,
} from "@/services/folders";
import { UNGROUPED_KEY } from "@/stores/folders";
import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  ALL_KEY,
  ARCHIVED_KEY,
  EMPTY_CONVERSATIONS,
  EMPTY_DELETED_CONVERSATIONS,
  EMPTY_DELETED_FOLDERS,
  STALE_DAYS,
  TRASH_KEY,
  byPinnedThenRecency,
  isSyntheticFilter,
} from "./constants";
import {
  isUngroupedLiveChat,
  liveChatInFolder,
  partitionLiveConversationCounts,
} from "./liveFolderCounts";

/**
 * Left-pane filter selection + deep-link routing from global search / CommandPalette.
 */
export function useConversationRouting() {
  const location = useLocation();
  const foldersAll = useFolders();
  const folders = useMemo(
    () => dedupeFoldersByLocalBinding(foldersAll),
    [foldersAll],
  );

  const [selected, setSelected] = useState<string>(ALL_KEY);
  const [flashId, setFlashId] = useState<string | null>(null);

  // Full id set (incl. historical duplicate bindings) so filters / deep-links keep working.
  const folderIds = useMemo(
    () => new Set(foldersAll.map((f) => f.id)),
    [foldersAll],
  );

  // A folder hit from global search jumps here via navigation state.
  // biome-ignore lint/correctness/useExhaustiveDependencies: location.key is the intentional per-navigation trigger.
  useEffect(() => {
    const state = location.state as {
      focusFolderId?: string;
      focusArchived?: boolean;
      focusTrash?: boolean;
    } | null;
    if (state?.focusArchived) {
      setSelected(ARCHIVED_KEY);
      return;
    }
    if (state?.focusTrash) {
      setSelected(TRASH_KEY);
      return;
    }
    const target = state?.focusFolderId;
    if (!target) return;
    // Flash the row the rail actually shows. A historical local-binding
    // duplicate is not its own row.
    const shown = canonicalFolderIds(foldersAll).get(target) ?? target;
    setSelected(shown);
    setFlashId(shown);
    const t = setTimeout(() => setFlashId(null), 1500);
    return () => clearTimeout(t);
  }, [location.key]);

  // Deleted folder → fall back to 全部对话.
  useEffect(() => {
    if (isSyntheticFilter(selected)) return;
    if (!folderIds.has(selected)) setSelected(ALL_KEY);
  }, [folderIds, selected]);

  return { selected, setSelected, flashId, folderIds, folders, foldersAll };
}

/**
 * Right-pane list: search, stale filter, folder scoping, per-folder counts.
 *
 * `folders` is the full catalog (including historical local-binding duplicates).
 * Counts roll those duplicates onto the displayed row.
 */
export function useConversationList(
  selected: string,
  folders: readonly FolderMeta[],
) {
  const conversations = useConversations();
  const [query, setQuery] = useState("");
  const [staleOnly, setStaleOnly] = useState(false);

  const isArchivedView = selected === ARCHIVED_KEY;
  const archivedQuery = useArchivedConversations(true);
  const archived = archivedQuery.data ?? EMPTY_CONVERSATIONS;

  // Fetched unconditionally like the archived list — the left rail shows a count
  // badge for「最近删除」whether or not that view is the selected one. Deleted chats
  // and deleted projects are two trips into one view; the badge counts both.
  const isTrashView = selected === TRASH_KEY;
  const trashQuery = useFolderTrash(true);
  const trash = trashQuery.data?.items ?? EMPTY_DELETED_FOLDERS;
  const convTrashQuery = useConversationTrash(true);
  const deletedConversations =
    convTrashQuery.data?.items ?? EMPTY_DELETED_CONVERSATIONS;
  // Both bins run on the same server-side ``workspace_retention_days``; take whichever
  // trip has landed rather than assuming a number the server never sent.
  const retentionDays =
    trashQuery.data?.retentionDays ??
    convTrashQuery.data?.retentionDays ??
    null;
  // ``total`` is the retention window. The arrays are a capped page, so the badge
  // and the empty-bin confirm follow the totals once a response has landed.
  const conversationTrashTotal =
    convTrashQuery.data?.total ?? deletedConversations.length;
  const folderTrashTotal = trashQuery.data?.total ?? trash.length;
  const trashCount = conversationTrashTotal + folderTrashTotal;

  const groupedSettled = useGroupedConversationsSettled();

  const counts = useMemo(
    () => partitionLiveConversationCounts(conversations, folders),
    [conversations, folders],
  );

  const list = useMemo(() => {
    const { canonical } = counts;
    const base = isArchivedView
      ? archived
      : conversations.filter((c) => {
          if (selected === ALL_KEY) return true;
          if (selected === UNGROUPED_KEY)
            return isUngroupedLiveChat(c.folderId, canonical);
          return liveChatInFolder(c.folderId, selected, canonical);
        });
    const q = query.trim().toLowerCase();
    let filtered = q
      ? base.filter((c) => c.title.toLowerCase().includes(q))
      : base;
    if (staleOnly && !isArchivedView) {
      const cutoff = Date.now() - STALE_DAYS * 86_400_000;
      filtered = filtered.filter(
        (c) => (Date.parse(c.updatedAt) || 0) < cutoff,
      );
    }
    return [...filtered].sort(byPinnedThenRecency);
  }, [
    conversations,
    archived,
    isArchivedView,
    selected,
    query,
    counts,
    staleOnly,
  ]);

  // 最近删除 holds deleted projects and deleted chats — same search box, own lists
  // (neither is a `Conversation`, so they can't ride the recency grouping above).
  const trashList = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? trash.filter((f) => f.name.toLowerCase().includes(q)) : trash;
  }, [trash, query]);

  const deletedConversationList = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q
      ? deletedConversations.filter((c) => c.title.toLowerCase().includes(q))
      : deletedConversations;
  }, [deletedConversations, query]);

  return {
    conversations,
    archived,
    counts,
    list,
    query,
    setQuery,
    staleOnly,
    setStaleOnly,
    isArchivedView,
    isTrashView,
    trashCount,
    trashList,
    deletedConversationList,
    retentionDays,
    conversationTrashTotal,
    folderTrashTotal,
    conversationTrashListed: deletedConversations.length,
    folderTrashListed: trash.length,
    groupedSettled,
  };
}
