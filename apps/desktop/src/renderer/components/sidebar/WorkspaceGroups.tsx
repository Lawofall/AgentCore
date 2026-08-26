import { SurfaceRowButton } from "@/components/ui";
import { useConversations } from "@/hooks/useConversations";
import { useWorkspaceGroups } from "@/hooks/useWorkspaceGroups";
import { deriveGroupWorkspaceIsLocal } from "@/lib/conversationWorkspaceMode";
import { isGroupExpanded, pickGroupVisible } from "@/lib/sidebarRailVisibility";
import { useRequiredConversationIds } from "@/stores/aiAttention";
import { useConversationStore } from "@/stores/conversation";
import { useSidebarStore } from "@/stores/sidebar";
import { MoreHorizontal } from "lucide-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { ConversationItem } from "./ConversationItem";
import { WorkspaceGroupHeader } from "./WorkspaceGroupHeader";
import {
  FolderGroupDragGhost,
  FolderGroupInsertLine,
  folderGroupInsertPlace,
} from "./folderGroupDragFeedback";
import { useFolderGroupReorder } from "./useFolderGroupReorder";

/**
 * The sidebar's **folder** zone (前端UX §一 方案C): collapsible per-folder groups
 * between「置顶」and「快速对话」, fed by {@link useWorkspaceGroups}. Group name =
 * folder name (双模式工作区 §5.4). Foldered chats live ONLY here when unpinned
 * (pinned lift to the rail 置顶区; 裸聊 stay in「快速对话」). Empty folders are
 * hidden; a folder whose every chat is pinned still renders its header so「+」/
 * 归档全部 remain reachable. The zone renders nothing when no group exists.
 *
 * Groups are **flat even though 我的文件 nests** — the sidebar answers「这段对话在
 * 哪个文件夹」, and a nested rail here would bury chats behind ancestor rows that
 * hold no chats of their own. Nesting lives on the files page; a nested folder is
 * told apart by its ancestor breadcrumb, not by indentation.
 *
 * Expand state persists per folder (`useSidebarStore.expandedSections`, keyed by
 * folderId): an explicit user toggle is stored; required 期间计算覆盖 persist
 * （盖过再折叠，不写回）。无 stored 时默认折叠，除非组里有未置顶的当前对话
 * （置顶当前对话已在置顶区）。Each group reuses
 * {@link ConversationItem} so rows keep the same status dot / rename / move /
 * archive behavior. Group headers expose folder actions via
 * {@link WorkspaceGroupHeader} (header receives full folder members incl. pinned).
 */
export function WorkspaceGroups() {
  const groups = useWorkspaceGroups();
  const conversations = useConversations();
  const hasPinned = conversations.some((c) => c.pinned);
  const currentId = useConversationStore((s) => s.currentConversationId);
  const expandedSections = useSidebarStore((s) => s.expandedSections);
  const setSection = useSidebarStore((s) => s.setSection);
  const requiredIds = useRequiredConversationIds();
  const navigate = useNavigate();

  const folderIds = useMemo(
    () => groups.map(({ folder }) => folder.id),
    [groups],
  );
  const { getItemProps, draggingId, overId, place, dragPreview } =
    useFolderGroupReorder(folderIds);

  const dragFolder = useMemo(
    () => groups.find((g) => g.folder.id === draggingId)?.folder ?? null,
    [draggingId, groups],
  );

  const activeFolderId = useMemo(() => {
    const active = conversations.find((c) => c.id === currentId);
    if (!active || active.pinned) return null;
    return active.folderId ?? null;
  }, [conversations, currentId]);

  if (groups.length === 0) return null;

  return (
    <>
      {hasPinned && <div className="mx-3 border-t border-sidebar-border" />}
      <div className="space-y-0.5 px-2 pb-1 pt-2">
        {groups.map(({ folder, convs }) => {
          const stored = expandedSections[folder.id];
          const visible = convs.filter((c) => !c.pinned);
          const hasRequired = visible.some((c) => requiredIds.has(c.id));
          const expanded = isGroupExpanded({
            stored,
            isActiveFolder: folder.id === activeFolderId,
            hasRequired,
          });
          const shown = pickGroupVisible(visible, requiredIds);
          const overflow = visible.length - shown.length;
          const groupIsLocal = deriveGroupWorkspaceIsLocal(folder);
          const insert = folderGroupInsertPlace(
            folder.id,
            draggingId,
            overId,
            place,
          );
          return (
            <div key={folder.id}>
              <div className="relative">
                {insert ? <FolderGroupInsertLine place={insert} /> : null}
                <WorkspaceGroupHeader
                  folder={folder}
                  convs={convs}
                  expanded={expanded}
                  onToggleExpanded={() => setSection(folder.id, !expanded)}
                  sortable={getItemProps(folder.id)}
                />
              </div>
              {expanded && (
                // Same icon column as group header / 裸聊 / top nav — no nested
                // indent (status dots & cloud icons must share that axis).
                <div className="space-y-0.5">
                  {shown.map((c) => (
                    <ConversationItem
                      key={c.id}
                      conversation={c}
                      groupIsLocal={groupIsLocal}
                      className="px-2"
                    />
                  ))}
                  {overflow > 0 && (
                    <SurfaceRowButton
                      onClick={() =>
                        navigate("/conversations", {
                          state: { focusFolderId: folder.id },
                        })
                      }
                      className="h-8 px-2 text-xs text-sidebar-foreground/50 hover:text-sidebar-foreground"
                    >
                      <MoreHorizontal size={13} className="shrink-0" />
                      更多（{overflow}）
                    </SurfaceRowButton>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {dragPreview && dragFolder ? (
        <FolderGroupDragGhost
          label={dragFolder.name}
          isLocal={deriveGroupWorkspaceIsLocal(dragFolder)}
          preview={dragPreview}
        />
      ) : null}
    </>
  );
}
