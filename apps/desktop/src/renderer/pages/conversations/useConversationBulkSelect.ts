import {
  useArchiveConversation,
  useDeleteConversation,
  useRestoreConversation,
  useUnarchiveConversation,
} from "@/hooks/useConversations";
import { notifyConversationDeleted } from "@/lib/conversationDeleteCopy";
import { notifyError } from "@/lib/toast";
import type { Conversation } from "@/stores/conversation";
import { useConversationStore } from "@/stores/conversation";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

export function useConversationBulkSelect(
  list: Conversation[],
  selectedFilter: string,
  _isArchivedView: boolean,
) {
  const navigate = useNavigate();
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());

  const archiveMutation = useArchiveConversation();
  const deleteMutation = useDeleteConversation();
  const restoreMutation = useRestoreConversation();
  const unarchiveMutation = useUnarchiveConversation();
  const dropConversationRuntime = useConversationStore(
    (s) => s.dropConversationRuntime,
  );
  const currentId = useConversationStore((s) => s.currentConversationId);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `selectedFilter` is the intentional re-run key.
  useEffect(() => {
    setSelectedIds(new Set());
  }, [selectedFilter]);

  const allVisibleSelected =
    list.length > 0 && list.every((c) => selectedIds.has(c.id));

  const toggleSelectAll = () => {
    if (allVisibleSelected) {
      setSelectedIds(new Set());
      return;
    }
    setSelectedIds(new Set(list.map((c) => c.id)));
  };

  const toggleSelected = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const exitSelectMode = () => {
    setSelectMode(false);
    setSelectedIds(new Set());
  };

  const notifyUndoForDeleted = (deletedIds: string[]) => {
    if (deletedIds.length === 0) return;
    const ids = [...deletedIds];
    notifyConversationDeleted(`${ids.length} 条`, () => {
      for (const id of ids) restoreMutation.mutate(id);
    });
  };

  const handleBulkArchive = async () => {
    const ids = [...selectedIds];
    for (const id of ids) {
      try {
        await archiveMutation.mutateAsync(id);
        dropConversationRuntime(id);
        if (id === currentId) navigate("/");
      } catch (err) {
        notifyError(err, "批量归档失败");
        return;
      }
    }
    exitSelectMode();
  };

  const handleBulkUnarchive = () => {
    for (const id of selectedIds) {
      unarchiveMutation.mutate(id, {
        onError: (err) => notifyError(err, "批量取消归档失败"),
      });
    }
    exitSelectMode();
  };

  const handleBulkDelete = async () => {
    const ids = [...selectedIds];
    const deletedIds: string[] = [];
    for (const id of ids) {
      try {
        await deleteMutation.mutateAsync(id);
        dropConversationRuntime(id);
        if (id === currentId) navigate("/");
        deletedIds.push(id);
      } catch (err) {
        notifyError(err, "批量删除失败");
        notifyUndoForDeleted(deletedIds);
        if (deletedIds.length > 0) {
          setSelectedIds((prev) => {
            const next = new Set(prev);
            for (const deleted of deletedIds) next.delete(deleted);
            return next;
          });
        }
        return;
      }
    }
    notifyUndoForDeleted(deletedIds);
    exitSelectMode();
  };

  return {
    selectMode,
    setSelectMode,
    selectedIds,
    allVisibleSelected,
    toggleSelectAll,
    toggleSelected,
    exitSelectMode,
    handleBulkArchive,
    handleBulkUnarchive,
    handleBulkDelete,
  };
}
