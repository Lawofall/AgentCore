import { queryClient } from "@/lib/queryClient";
import { conversationKeys, folderKeys, workspaceKeys } from "@/lib/queryKeys";
import { notifyError, notifyInfo } from "@/lib/toast";
import { emptyConversationTrash } from "@/services/conversations";
import { emptyFolderTrash } from "@/services/folders";
import { scheduleAccountRulesMemoryRefresh } from "@/services/refreshAccountRulesMemory";
import { useMutation } from "@tanstack/react-query";

/**
 * Empty both halves of「最近删除」.
 *
 * Conversations go first so a chat that also sits in a deleted folder still gets
 * its scratch wipe; the folder pass then takes the desks. A folder a turn still
 * holds stays in the bin — the rest are already gone, and the toast says so.
 */
export function useEmptyRecentlyDeleted() {
  return useMutation({
    mutationFn: async () => {
      const conversations = await emptyConversationTrash();
      const folders = await emptyFolderTrash();
      return { conversations, folders };
    },
    onError: (err) => notifyError(err, "清空失败"),
    onSuccess: ({ folders }) => {
      if (folders.skippedBusy > 0) {
        notifyInfo(`${folders.skippedBusy} 个文件夹正在使用，稍后再清空`);
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: conversationKeys.trash });
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
