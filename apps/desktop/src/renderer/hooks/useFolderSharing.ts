import { queryClient } from "@/lib/queryClient";
import { conversationKeys, folderKeys, workspaceKeys } from "@/lib/queryKeys";
import {
  type FolderInviteRole,
  type FolderMeta,
  acceptFolderInvite,
  changeFolderMemberRole,
  inviteFolderMember,
  isFolderOwner,
  listFolderMembers,
  listFoldersSharedWithMe,
  listPendingFolderInvites,
  rejectFolderInvite,
  removeOrLeaveFolderMember,
} from "@/services/folders";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { GroupedConversations } from "./useConversations";

/** Cloud desks the current user joined (editor / viewer). */
export function useSharedWithMeFolders() {
  return useQuery({
    queryKey: folderKeys.sharedWithMe,
    queryFn: listFoldersSharedWithMe,
    staleTime: 30_000,
  });
}

/** Pending folder invites for the current user. */
export function usePendingFolderInvites(enabled = true) {
  return useQuery({
    queryKey: folderKeys.pendingInvites,
    queryFn: listPendingFolderInvites,
    staleTime: 30_000,
    enabled,
  });
}

export function useFolderMembers(folderId: string | null) {
  return useQuery({
    queryKey: folderKeys.members(folderId ?? ""),
    queryFn: () => {
      if (!folderId) return Promise.resolve([]);
      return listFolderMembers(folderId);
    },
    enabled: !!folderId,
    staleTime: 15_000,
  });
}

export function invalidateFolderSharing(folderId?: string): void {
  void queryClient.invalidateQueries({ queryKey: folderKeys.sharedWithMe });
  void queryClient.invalidateQueries({ queryKey: folderKeys.pendingInvites });
  void queryClient.invalidateQueries({ queryKey: conversationKeys.grouped });
  void queryClient.invalidateQueries({ queryKey: workspaceKeys.list });
  if (folderId) {
    void queryClient.invalidateQueries({
      queryKey: folderKeys.members(folderId),
    });
  }
}

/** Firehose catch-up: every collaboration-desk query. */
export function invalidateAllFolderSharing(): void {
  void queryClient.invalidateQueries({ queryKey: folderKeys.all });
  void queryClient.invalidateQueries({ queryKey: conversationKeys.grouped });
  void queryClient.invalidateQueries({ queryKey: workspaceKeys.list });
}

export function useAcceptFolderInvite() {
  return useMutation({
    mutationFn: (folderId: string) => acceptFolderInvite(folderId),
    onSuccess: (folder) => {
      queryClient.setQueryData<GroupedConversations>(
        conversationKeys.grouped,
        (old) => {
          const base = old ?? { folders: [], conversations: [] };
          return {
            ...base,
            folders: [
              folder,
              ...base.folders.filter((f) => f.id !== folder.id),
            ],
          };
        },
      );
      invalidateFolderSharing(folder.id);
    },
  });
}

export function useRejectFolderInvite() {
  return useMutation({
    mutationFn: (folderId: string) => rejectFolderInvite(folderId),
    onSuccess: (_data, folderId) => invalidateFolderSharing(folderId),
  });
}

export function useInviteFolderMember() {
  return useMutation({
    mutationFn: ({
      folderId,
      userId,
      role,
    }: {
      folderId: string;
      userId: string;
      role: FolderInviteRole;
    }) => inviteFolderMember(folderId, userId, role),
    onSuccess: (_data, vars) => invalidateFolderSharing(vars.folderId),
  });
}

export function useChangeFolderMemberRole() {
  return useMutation({
    mutationFn: ({
      folderId,
      memberUserId,
      role,
    }: {
      folderId: string;
      memberUserId: string;
      role: FolderInviteRole;
    }) => changeFolderMemberRole(folderId, memberUserId, role),
    onSuccess: (_data, vars) => invalidateFolderSharing(vars.folderId),
  });
}

export function useRemoveOrLeaveFolderMember() {
  return useMutation({
    mutationFn: ({
      folderId,
      memberUserId,
    }: {
      folderId: string;
      memberUserId: string;
    }) => removeOrLeaveFolderMember(folderId, memberUserId),
    onSuccess: (_data, vars) => {
      queryClient.setQueryData<GroupedConversations>(
        conversationKeys.grouped,
        (old) => {
          if (!old) return old;
          const mine = old.folders.find((f) => f.id === vars.folderId);
          if (!mine || isFolderOwner(mine)) return old;
          return {
            ...old,
            folders: old.folders.filter((f) => f.id !== vars.folderId),
          };
        },
      );
      invalidateFolderSharing(vars.folderId);
    },
  });
}

/** Patch a shared-with-me row after accept (optimistic name/role). */
export function patchSharedWithMeInCache(
  folderId: string,
  patch: Partial<FolderMeta>,
): void {
  const cur = queryClient.getQueryData<FolderMeta[]>(folderKeys.sharedWithMe);
  if (!cur) return;
  queryClient.setQueryData<FolderMeta[]>(
    folderKeys.sharedWithMe,
    cur.map((f) => (f.id === folderId ? { ...f, ...patch } : f)),
  );
}
