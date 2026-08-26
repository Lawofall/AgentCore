import { DeleteFolderDialog } from "@/components/folders/DeleteFolderDialog";
import {
  IconButton,
  NO_TAB_DRAG_ATTR,
  type SortableTabItemProps,
  SurfaceRow,
} from "@/components/ui";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useArchiveConversation } from "@/hooks/useConversations";
import {
  releaseFolderConversations,
  useDeleteFolder,
  usePermanentDeleteFolder,
  useRestoreFolder,
} from "@/hooks/useFolders";
import { deriveGroupWorkspaceIsLocal } from "@/lib/conversationWorkspaceMode";
import { folderAncestorNames } from "@/lib/folderTree";
import { startNewConversation } from "@/lib/newConversation";
import { notifyError, notifyInfo } from "@/lib/toast";
import { cn } from "@/lib/utils";
import type { FolderMeta } from "@/services/folders";
import type { Conversation } from "@/stores/conversation";
import { useConversationStore } from "@/stores/conversation";
import { useFoldersStore } from "@/stores/folders";
import {
  Archive,
  ChevronRight,
  FolderOpen,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Trash2,
  Upload,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GroupWorkspaceModeIcon } from "./ConversationWorkspaceModeIcon";

interface Props {
  folder: FolderMeta;
  /** Every live conversation in this folder (not just the sidebar Top-N slice). */
  convs: Conversation[];
  expanded: boolean;
  onToggleExpanded: () => void;
  /** Narrow drawer: no 查看全部 / 删夹 / 导入；＋始终可见。权威 → 前端技术 §五. */
  surface?: "wide" | "narrow";
  /** Vertical reorder handle; omit when the row is not in a sortable list. */
  sortable?: SortableTabItemProps;
}

/**
 * Sidebar folder-group header: left icon slot (cloud/local; hover overlays
 * chevron for collapse) + name + hover「+」new chat in this folder +「⋯」.
 * Right-click and hover「⋯」share the same menu;「归档全部对话」maps to
 * batch conversation archive (no `Folder.archived`).
 *
 * A nested folder shows its ancestor breadcrumb under the name — the zone is
 * flat, so「设计 / 图标」is the only thing telling two 图标 folders apart.
 */
export function WorkspaceGroupHeader({
  folder,
  convs,
  expanded,
  onToggleExpanded,
  surface = "wide",
  sortable,
}: Props) {
  const narrow = surface === "narrow";
  const [moreOpen, setMoreOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const navigate = useNavigate();
  const archiveMutation = useArchiveConversation();
  const deleteFolderMutation = useDeleteFolder();
  const permanentDeleteMutation = usePermanentDeleteFolder();
  const restoreFolderMutation = useRestoreFolder();
  const currentId = useConversationStore((s) => s.currentConversationId);
  const dropConversationRuntime = useConversationStore(
    (s) => s.dropConversationRuntime,
  );

  const liveConvCount = convs.length;
  const groupIsLocal = useMemo(
    () => deriveGroupWorkspaceIsLocal(folder),
    [folder],
  );
  /** 「设计 / 图标」— only for a nested folder; the zone itself stays flat. */
  const ancestorLabel = useMemo(
    () => folderAncestorNames(folder).join(" / "),
    [folder],
  );

  const viewAllConversations = () => {
    navigate("/conversations", { state: { focusFolderId: folder.id } });
  };

  const browseFiles = () => {
    navigate("/files", { state: { focusWsId: `folder:${folder.id}` } });
  };

  const newChatInFolder = () => {
    setMoreOpen(false);
    startNewConversation(navigate, folder.id);
  };

  /** Quiet optional convert — same as Composer「导入本机文件夹到云」（非催债）. */
  const openImportToCloud = () => {
    setMoreOpen(false);
    useFoldersStore.getState().openImportToCloud({
      rootId: folder.localRootId ?? undefined,
      folderName: folder.name,
    });
  };

  const handleArchiveAll = async () => {
    setMoreOpen(false);
    for (const { id } of convs) {
      try {
        await archiveMutation.mutateAsync(id);
      } catch (err) {
        notifyError(err, "批量归档失败");
        return;
      }
      dropConversationRuntime(id);
      if (id === currentId) navigate("/");
    }
  };

  /**
   * 撤销 is the main way back from a mistaken delete — most people notice within
   * seconds and never open「最近删除」. The header unmounts the moment the folder
   * leaves the sidebar, so the toast is raised from the awaited handler (not a
   * `mutate` callback, which React Query drops for an unmounted observer).
   */
  const confirmDeleteFolder = async () => {
    const name = folder.name;
    try {
      await deleteFolderMutation.mutateAsync(folder.id);
    } catch (err) {
      notifyError(err, "删除文件夹失败");
      return;
    }
    setDeleteOpen(false);
    const leftActive = releaseFolderConversations(folder.id, {
      dropRuntime: dropConversationRuntime,
      currentId,
    });
    if (leftActive) navigate("/");
    notifyInfo("已删除文件夹", {
      description: name,
      duration: 8000,
      action: {
        label: "撤销",
        onClick: () => restoreFolderMutation.mutate({ id: folder.id, name }),
      },
    });
  };

  const confirmPermanentDelete = () => {
    for (const { id } of convs) {
      dropConversationRuntime(id);
      if (id === currentId) navigate("/");
    }
    permanentDeleteMutation.mutate(folder.id, {
      onSuccess: () => setDeleteOpen(false),
      onError: (err) => notifyError(err, "彻底删除失败"),
    });
  };

  const archiveLabel =
    liveConvCount > 0 ? `归档全部对话 (${liveConvCount})` : "归档全部对话";

  const newChatLabel = groupIsLocal
    ? "在此本机文件夹中新开对话"
    : "在此文件夹中新开对话";

  const showImport = groupIsLocal && !narrow;
  const importMenuItem = showImport ? (
    <>
      <ContextMenuItem onSelect={openImportToCloud}>
        <Upload size={14} className="shrink-0" />
        <span className="flex-1 truncate">导入到「我的文件」</span>
      </ContextMenuItem>
      <ContextMenuSeparator />
    </>
  ) : null;

  const importDropdownItem = showImport ? (
    <>
      <DropdownMenuItem onSelect={openImportToCloud}>
        <Upload size={14} className="shrink-0" />
        <span className="flex-1 truncate">导入到「我的文件」</span>
      </DropdownMenuItem>
      <DropdownMenuSeparator />
    </>
  ) : null;

  const menuItems = (
    <>
      {importMenuItem}
      <ContextMenuItem onSelect={newChatInFolder}>
        <Plus size={14} className="shrink-0" />
        <span className="flex-1 truncate">新建对话</span>
      </ContextMenuItem>
      {!narrow && (
        <ContextMenuItem onSelect={viewAllConversations}>
          <MessageSquare size={14} className="shrink-0" />
          <span className="flex-1 truncate">查看全部对话</span>
        </ContextMenuItem>
      )}
      {(!narrow || !groupIsLocal) && (
        <ContextMenuItem onSelect={browseFiles}>
          <FolderOpen size={14} className="shrink-0" />
          <span className="flex-1 truncate">浏览文件</span>
        </ContextMenuItem>
      )}
      {!narrow && (
        <>
          <ContextMenuSeparator />
          <ContextMenuItem
            disabled={liveConvCount === 0}
            onSelect={() => void handleArchiveAll()}
          >
            <Archive size={14} className="shrink-0" />
            <span className="flex-1 truncate">{archiveLabel}</span>
          </ContextMenuItem>
          <ContextMenuSeparator />
          <ContextMenuItem
            variant="danger"
            onSelect={() => setDeleteOpen(true)}
          >
            <Trash2 size={14} className="shrink-0" />
            <span className="flex-1 truncate">删除文件夹…</span>
          </ContextMenuItem>
        </>
      )}
    </>
  );

  const dropdownItems = (
    <>
      {importDropdownItem}
      <DropdownMenuItem onSelect={newChatInFolder}>
        <Plus size={14} className="shrink-0" />
        <span className="flex-1 truncate">新建对话</span>
      </DropdownMenuItem>
      {!narrow && (
        <DropdownMenuItem onSelect={viewAllConversations}>
          <MessageSquare size={14} className="shrink-0" />
          <span className="flex-1 truncate">查看全部对话</span>
        </DropdownMenuItem>
      )}
      {(!narrow || !groupIsLocal) && (
        <DropdownMenuItem onSelect={browseFiles}>
          <FolderOpen size={14} className="shrink-0" />
          <span className="flex-1 truncate">浏览文件</span>
        </DropdownMenuItem>
      )}
      {!narrow && (
        <>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={liveConvCount === 0}
            onSelect={() => void handleArchiveAll()}
          >
            <Archive size={14} className="shrink-0" />
            <span className="flex-1 truncate">{archiveLabel}</span>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="danger"
            onSelect={() => setDeleteOpen(true)}
          >
            <Trash2 size={14} className="shrink-0" />
            <span className="flex-1 truncate">删除文件夹…</span>
          </DropdownMenuItem>
        </>
      )}
    </>
  );

  const rowActionClass =
    "size-6 text-sidebar-foreground/40 hover:text-sidebar-foreground";
  const { className: sortableClassName, ...sortableRest } = sortable ?? {};

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>
          <SurfaceRow
            variant="sidebar"
            className={cn(
              "group h-8 px-2 text-sidebar-foreground/70 hover:text-sidebar-foreground",
              sortableClassName,
            )}
            {...sortableRest}
          >
            {/* biome-ignore lint/a11y/useSemanticElements: 行内嵌 DropdownMenuTrigger 的真 <button>，此可点击区不可套 <button>。 */}
            <div
              role="button"
              tabIndex={0}
              aria-expanded={expanded}
              className="flex min-w-0 flex-1 items-center gap-2 text-left"
              onClick={onToggleExpanded}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onToggleExpanded();
                }
              }}
            >
              <span className="relative inline-flex size-3.5 shrink-0 items-center justify-center">
                <span className="absolute inset-0 flex items-center justify-center opacity-100 transition-opacity group-hover:pointer-events-none group-hover:opacity-0">
                  <GroupWorkspaceModeIcon isLocal={groupIsLocal} />
                </span>
                <ChevronRight
                  size={14}
                  aria-hidden
                  className={`absolute text-sidebar-foreground/40 opacity-0 transition-[opacity,transform] group-hover:opacity-100 ${
                    expanded ? "rotate-90" : ""
                  }`}
                />
              </span>
              <span className="flex min-w-0 flex-1 flex-col justify-center">
                <span className="truncate">{folder.name}</span>
                {ancestorLabel && (
                  <span className="truncate text-xs leading-tight text-sidebar-foreground/40">
                    {ancestorLabel}
                  </span>
                )}
              </span>
            </div>
            <span
              {...{ [NO_TAB_DRAG_ATTR]: "" }}
              className={`flex shrink-0 items-center gap-0.5 ${
                narrow || moreOpen
                  ? "opacity-100"
                  : "opacity-0 transition-opacity group-hover:opacity-100"
              }`}
            >
              <DropdownMenu open={moreOpen} onOpenChange={setMoreOpen}>
                <DropdownMenuTrigger asChild>
                  <IconButton
                    tone="sidebar"
                    aria-label="文件夹操作"
                    title="更多"
                    className={rowActionClass}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <MoreHorizontal size={13} />
                  </IconButton>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  className="min-w-52"
                  onClick={(e) => e.stopPropagation()}
                >
                  {dropdownItems}
                </DropdownMenuContent>
              </DropdownMenu>
              <IconButton
                tone="sidebar"
                aria-label={newChatLabel}
                title={newChatLabel}
                className={rowActionClass}
                onClick={(e) => {
                  e.stopPropagation();
                  newChatInFolder();
                }}
              >
                <Plus size={13} />
              </IconButton>
            </span>
          </SurfaceRow>
        </ContextMenuTrigger>
        <ContextMenuContent className="min-w-52">
          {menuItems}
        </ContextMenuContent>
      </ContextMenu>
      <DeleteFolderDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        name={folder.name}
        liveConvCount={liveConvCount}
        isLocal={groupIsLocal}
        onConfirm={() => void confirmDeleteFolder()}
        onPermanentConfirm={confirmPermanentDelete}
      />
    </>
  );
}
