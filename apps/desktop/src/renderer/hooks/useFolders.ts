import type { GroupedConversations } from "@/hooks/useConversations";
import {
  getConversations,
  removeConversationFromCache,
  useGroupedConversations,
} from "@/hooks/useConversations";
import { queryClient } from "@/lib/queryClient";
import { conversationKeys, folderKeys, workspaceKeys } from "@/lib/queryKeys";
import { notifyError, notifyInfo } from "@/lib/toast";
import {
  type CreateFolderInput,
  type FolderMeta,
  type FolderTrash,
  createFolder,
  deleteFolder,
  listFolderTrash,
  permanentDeleteFolder,
  restoreFolder,
  updateFolder,
} from "@/services/folders";
import { scheduleAccountRulesMemoryRefresh } from "@/services/refreshAccountRulesMemory";
import { useMutation, useQuery } from "@tanstack/react-query";

/**
 * Folders as React Query data — folders share the `/grouped` query (and its
 * cache entry) with conversations, so this reads/writes the `folders` half of
 * that same entry. Pure-UI folder state (pending rename, draft workspace intent)
 * stays in the zustand folders store; only the server-owned list lives here.
 */
const EMPTY_FOLDERS: FolderMeta[] = [];

/** Imperative read of the cached folder list (for non-React callers). */
export function getFolders(): FolderMeta[] {
  return (
    queryClient.getQueryData<GroupedConversations>(conversationKeys.grouped)
      ?.folders ?? []
  );
}

/** Rewrite the cached folder list, leaving the conversations half untouched. */
function writeFolders(updater: (list: FolderMeta[]) => FolderMeta[]): void {
  queryClient.setQueryData<GroupedConversations>(
    conversationKeys.grouped,
    (old) => {
      const base = old ?? { folders: [], conversations: [] };
      return { ...base, folders: updater(base.folders) };
    },
  );
}

/** Prepend a folder (newest first, before the server reorders on reload). */
export function addFolderCache(folder: FolderMeta): void {
  writeFolders((list) => [folder, ...list.filter((f) => f.id !== folder.id)]);
}

/** Shallow-merge a patch onto one cached folder (no-op if absent). */
export function patchFolderCache(id: string, patch: Partial<FolderMeta>): void {
  writeFolders((list) =>
    list.map((f) => (f.id === id ? { ...f, ...patch } : f)),
  );
}

/** Drop a folder from the cached list. */
export function removeFolderFromCache(id: string): void {
  writeFolders((list) => list.filter((f) => f.id !== id));
}

/** Reactive folder list (server-ordered). */
export function useFolders(): FolderMeta[] {
  return useGroupedConversations().data?.folders ?? EMPTY_FOLDERS;
}

/** Create a folder (= workspace), then add it to the cache. */
export function useCreateFolder() {
  return useMutation({
    mutationFn: (input: CreateFolderInput) => createFolder(input),
    onSuccess: ({ folder }) => {
      addFolderCache(folder);
    },
  });
}

/**
 * Rename or re-parent a folder, optimistic with rollback on failure.
 *
 * A move changes `relPath` for the folder *and its whole subtree*, which only
 * the server can compute — the optimistic patch covers the name (what the row
 * shows) and lets the refetched list settle the paths.
 */
export function useUpdateFolder() {
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: { name?: string; parentId?: string | null };
    }) => updateFolder(id, patch),
    onMutate: ({ id, patch }) => {
      const prev = getFolders().find((f) => f.id === id) ?? null;
      const cachePatch: Partial<FolderMeta> = {};
      if (patch.name !== undefined) cachePatch.name = patch.name;
      patchFolderCache(id, cachePatch);
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        patchFolderCache(ctx.prev.id, { name: ctx.prev.name });
      }
    },
    onSuccess: (_data, { patch }) => {
      if (patch.parentId === undefined) return;
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.grouped,
      });
    },
  });
}

/**
 * The renderer-side fallout of a folder leaving the live list: its member
 * conversations are gone from the sidebar already (soft-delete archives them
 * server-side in one statement), so drop their cached rows and unload their
 * runtime slices. Returns true when one of them was the conversation on screen —
 * the caller owns the navigation away from it.
 *
 * Deliberately *not* an archive loop: routing these through the archive endpoint
 * would stamp them as「用户主动归档」and the server could no longer tell which
 * conversations to un-archive when the folder is restored.
 */
export function releaseFolderConversations(
  folderId: string,
  {
    dropRuntime,
    currentId,
  }: { dropRuntime: (id: string) => void; currentId: string | null },
): boolean {
  let releasedActive = false;
  for (const { id, folderId: owner } of getConversations()) {
    if (owner !== folderId) continue;
    removeConversationFromCache(id);
    dropRuntime(id);
    if (id === currentId) releasedActive = true;
  }
  return releasedActive;
}

/**
 * Soft-delete a folder. Server archives member conversations (不解组);
 * drop the folder from cache and refresh conversation lists.
 */
export function useDeleteFolder() {
  return useMutation({
    mutationFn: (id: string) => deleteFolder(id),
    onSuccess: (_data, id) => {
      removeFolderFromCache(id);
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.grouped,
      });
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.archived,
      });
      void queryClient.invalidateQueries({ queryKey: folderKeys.trash });
      void queryClient.invalidateQueries({ queryKey: workspaceKeys.list });
      scheduleAccountRulesMemoryRefresh();
    },
  });
}

/** 最近删除 — the recoverable projects + the retention window they live under. */
export function useFolderTrash(enabled: boolean) {
  return useQuery<FolderTrash>({
    queryKey: folderKeys.trash,
    queryFn: listFolderTrash,
    enabled,
    staleTime: 30_000,
  });
}

/**
 * Restore a deleted project (撤销删除 / 最近删除). Members that the delete
 * archived come back with it, so every conversation view is refreshed.
 *
 * Pass the name it was deleted under: the server re-allocates the tree slot, so
 * a project whose name was taken meanwhile returns as「名字 (2)」and the user is
 * told rather than left to spot it in the sidebar.
 *
 * Both toasts belong to the hook because the usual trigger is the delete toast's
 * 撤销 — by then the row that started this is unmounted, and React Query skips
 * per-call callbacks for a dead observer. A project swept past retention answers
 * 409 and that must always reach the user, never be retried or reconciled.
 */
export function useRestoreFolder() {
  return useMutation({
    mutationFn: ({ id }: { id: string; name: string }) => restoreFolder(id),
    onError: (err) => notifyError(err, "恢复失败"),
    onSuccess: (folder, { name }) => {
      addFolderCache(folder);
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.grouped,
      });
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.archived,
      });
      void queryClient.invalidateQueries({ queryKey: folderKeys.trash });
      void queryClient.invalidateQueries({ queryKey: workspaceKeys.list });
      scheduleAccountRulesMemoryRefresh();
      if (folder.name !== name) {
        notifyInfo("文件夹已恢复", {
          description: `原名已被占用，已恢复为「${folder.name}」`,
        });
      }
    },
  });
}

/** 彻底删除文件夹 — hard-delete folder and all member chats. */
export function usePermanentDeleteFolder() {
  return useMutation({
    mutationFn: (id: string) => permanentDeleteFolder(id),
    onSuccess: (_data, id) => {
      for (const c of getConversations()) {
        if (c.folderId === id) removeConversationFromCache(c.id);
      }
      removeFolderFromCache(id);
      void queryClient.invalidateQueries({
        queryKey: conversationKeys.archived,
      });
      scheduleAccountRulesMemoryRefresh();
    },
  });
}
