import { useSortableTabIds } from "@/components/ui";
import { useSidebarStore } from "@/stores/sidebar";

/**
 * Vertical reorder for visible folder-group headers. Commits the current
 * on-screen folder id list via `reorderFolderGroups` — never mutates
 * conversation `folder_id`, and never targets 置顶 / 裸聊 rows.
 */
export function useFolderGroupReorder(folderIds: readonly string[]) {
  const reorderFolderGroups = useSidebarStore((s) => s.reorderFolderGroups);
  return useSortableTabIds(folderIds, reorderFolderGroups, {
    axis: "y",
    idleGrabCursor: false,
    disabled: folderIds.length < 2,
    draggingClassName: "cursor-grabbing opacity-40",
  });
}
