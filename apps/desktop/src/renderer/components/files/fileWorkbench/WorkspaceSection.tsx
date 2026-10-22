import { CloneRepoDialog } from "@/components/files/CloneRepoDialog";
import { FileTree, type FileTreeHandle } from "@/components/files/FileTree";
import { isUntitledFolderName } from "@/components/files/dedupeName";
import {
  type TreeAction,
  runTreeAction,
} from "@/components/files/fileTreeActions";
import type { FileSortBy } from "@/components/files/fileTreeTypes";
import { IconButton } from "@/components/files/parts";
import { DeleteFolderDialog } from "@/components/folders/DeleteFolderDialog";
import { FolderCollabMark } from "@/components/folders/FolderCollabMark";
import { FolderMembersDialog } from "@/components/folders/FolderMembersDialog";
import { Button, ConfirmDialog } from "@/components/ui";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  getConversations,
  useDeleteConversation,
  useRenameConversation,
  useRestoreConversation,
} from "@/hooks/useConversations";
import {
  getFolders,
  releaseFolderConversations,
  useDeleteFolder,
  usePermanentDeleteFolder,
  useRestoreFolder,
  useUpdateFolder,
} from "@/hooks/useFolders";
import { removeConversationScratch } from "@/hooks/useWorkspaces";
import { notifyConversationDeleted } from "@/lib/conversationDeleteCopy";
import { useConversationLocationId } from "@/lib/conversationLocation";
import type { FileSource } from "@/lib/fileSource";
import { queryClient } from "@/lib/queryClient";
import { workspaceKeys } from "@/lib/queryKeys";
import { notifyActionError, notifyError, notifyInfo } from "@/lib/toast";
import { cn } from "@/lib/utils";
import {
  canShareFolder,
  canWriteFolder,
  folderHasCollaborators,
  folderMyRole,
  folderRoleLabel,
  isFolderOwner,
} from "@/services/folders";
import type { WorkspaceInfo } from "@/services/workspaces";
import {
  useConversationGenerating,
  useConversationStore,
} from "@/stores/conversation";
import { useFoldersStore } from "@/stores/folders";
import {
  ChevronDown,
  ChevronRight,
  Cloud,
  Eraser,
  FilePlus,
  Folder,
  FolderOpen,
  FolderPlus,
  FolderSearch,
  FolderUp,
  GitBranch,
  HardDrive,
  History,
  MessageSquare,
  Pencil,
  Trash2,
  Upload,
  Users,
} from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  WS_TRASH_PATH,
  WS_VERSIONS_PATH,
  conversationIdOf,
  folderIdOf,
  workspacePanelTabName,
} from "./storage";

/**
 * One workspace root = a **collapsible section**: a header (chevron + name +
 * create buttons) with its file tree shown beneath **only when expanded**.
 * Folders (`folder:<id>`) may also render nested folder rows (`nested`) and
 * folder-scope entries inside the tree's ``.agentcore`` row (`renderWorkroomLead`).
 *
 * - `folder:<id>` 文件夹：右键可重命名，所有者另有「删除文件夹…」（与侧栏
 *   {@link WorkspaceGroupHeader} 同一确认；树内「删除」仍是工作区软删，不是删整夹）。
 *   本机 `conv:` 有用户可见文件时进「本机文件夹」（改名=对话标题，删除=删对话）；云端 `conv:` 不列。
 */
export function WorkspaceSection({
  ws,
  source,
  activePath,
  expanded,
  onToggle,
  onOpenFile,
  flashing,
  renderWorkroomLead,
  forceExpandWorkroom = false,
  onWorkroomRevealApplied,
  onCreateWorkroomEntry,
  nested,
  depth = 0,
  hideRootDirs,
  showLocationBadge = true,
  offlineCloud = false,
  filterQuery = "",
  sortBy,
}: {
  ws: WorkspaceInfo;
  source: FileSource | null;
  activePath: string | null;
  expanded: boolean;
  onToggle: () => void;
  onOpenFile: (path: string, name: string) => void;
  flashing: boolean;
  /** Folder-scope entries, rendered inside the expanded ``.agentcore`` row. */
  renderWorkroomLead?: (indent: number) => ReactNode;
  /**
   * 「新建条目」on the tree's ``.agentcore`` header — always visible.
   * Returning `false` skips expanding the drawer after a failed create.
   */
  onCreateWorkroomEntry?: () => boolean | Promise<boolean>;
  forceExpandWorkroom?: boolean;
  onWorkroomRevealApplied?: () => void;
  /** Nested folder rows (我的文件 tree), rendered directly under the header. */
  nested?: ReactNode;
  /** Nesting level inside 我的文件 — indents the header and its tree. */
  depth?: number;
  /** Forwarded to {@link FileTree}: child-folder dirs already shown as rail rows. */
  hideRootDirs?: readonly string[];
  /** Off inside 我的文件 / 本机文件夹 — the section header already says which. */
  showLocationBadge?: boolean;
  /** N4-A: cloud workspace while read-only offline — grey + hint, keep visible. */
  offlineCloud?: boolean;
  /** Forwarded to {@link FileTree} for path/name filter (hub search box). */
  filterQuery?: string;
  /** Forwarded to {@link FileTree}: 名称 / 大小 / 修改时间排序（中枢顶栏统一选）。 */
  sortBy?: FileSortBy;
}) {
  const conversationId = conversationIdOf(ws.wsId);
  const folderId = folderIdOf(ws.wsId);
  const isLocal = ws.location === "local";
  const localUnavailable = isLocal && !source && !offlineCloud;
  const navigate = useNavigate();

  const rootRef = useRef<HTMLDivElement>(null);
  const treeRef = useRef<FileTreeHandle>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const skipBlurRef = useRef(false);
  /** First rename after untitled cloud-desk create; Esc deletes the empty desk. */
  const justCreatedFolderRef = useRef(false);
  const [pendingAction, setPendingAction] = useState<TreeAction | null>(null);
  const [clearScratchOpen, setClearScratchOpen] = useState(false);
  const [clearingScratch, setClearingScratch] = useState(false);
  const [cloneOpen, setCloneOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const pendingRenameFolderId = useFoldersStore((s) => s.pendingRenameFolderId);
  const clearPendingRename = useFoldersStore((s) => s.clearPendingRename);
  const [draft, setDraft] = useState(ws.name);
  const [membersOpen, setMembersOpen] = useState(false);
  const [deleteFolderOpen, setDeleteFolderOpen] = useState(false);

  const deleteMutation = useDeleteConversation();
  const deleteFolderMutation = useDeleteFolder();
  const permanentDeleteFolderMutation = usePermanentDeleteFolder();
  const restoreFolderMutation = useRestoreFolder();
  const restoreConversationMutation = useRestoreConversation();
  const renameMutation = useRenameConversation();
  const renameFolderMutation = useUpdateFolder();
  const locationId = useConversationLocationId();
  const dropConversationRuntime = useConversationStore(
    (s) => s.dropConversationRuntime,
  );
  /** Cloud conv scratch only — local/folder roots never get root-level clear. */
  const canClearScratch =
    !!conversationId && !isLocal && !offlineCloud && !!source?.caps.edit;
  /** Cloud workspace only — clone API requires cloud location. */
  const canClone = !isLocal && !offlineCloud && !!source?.caps.edit;
  /**
   * 云端版本 + 软删区走 REST（caps.snapshots）；服务端对本机一律 409。
   * 本机「版本」走 sidecar / 盘上 `AgentCore/versions`，同一菜单项、另一条后端。
   * 本机回收站仍是系统轨，不在这里冒充云端软删区。
   */
  const canCloudSnapshot =
    !isLocal && !offlineCloud && !!source?.caps.snapshots;
  const canLocalVersions = isLocal && !localUnavailable && !!ws.rootId;
  const canVersions = canCloudSnapshot || canLocalVersions;
  const canTrash = canCloudSnapshot;
  const scratchGenerating = useConversationGenerating(conversationId ?? "");

  const folder = folderId
    ? (getFolders().find((f) => f.id === folderId) ?? null)
    : null;
  const shareable = folder ? canShareFolder(folder) : false;
  // Missing cache row is not "I own this" — members would otherwise see owner-only rename.
  const folderOwner = Boolean(folder && isFolderOwner(folder));
  const folderRole = folder ? folderMyRole(folder) : "owner";
  /** 删的是文件夹这个容器，不是树里的一个文件。仅所有者；与侧栏同一道门。 */
  const canDeleteFolder = Boolean(folder && folderOwner);
  /** Nested cloud desk under this row — not mkdir inside the workspace tree. */
  const canNestCloudFolder = Boolean(
    folder &&
      folder.mode === "cloud" &&
      canWriteFolder(folder) &&
      !offlineCloud,
  );
  const memberConversations = () =>
    folder ? getConversations().filter((c) => c.folderId === folder.id) : [];

  useEffect(() => {
    if (flashing) rootRef.current?.scrollIntoView({ block: "nearest" });
  }, [flashing]);

  useEffect(() => {
    if (!expanded || !pendingAction) return;
    runTreeAction(treeRef.current, pendingAction);
    setPendingAction(null);
  }, [expanded, pendingAction]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  useEffect(() => {
    if (!folderId || folderId !== pendingRenameFolderId) return;
    setDraft(ws.name);
    setEditing(true);
    justCreatedFolderRef.current = isUntitledFolderName(ws.name);
    clearPendingRename();
  }, [folderId, pendingRenameFolderId, ws.name, clearPendingRename]);

  useEffect(() => {
    if (!editing) setDraft(ws.name);
  }, [ws.name, editing]);

  const requestTreeAction = (action: TreeAction) => {
    if (expanded) {
      runTreeAction(treeRef.current, action);
    } else {
      setPendingAction(action);
      onToggle();
    }
  };

  const revealRoot = async () => {
    try {
      await source?.revealInOsFileManager?.("");
    } catch (e) {
      notifyActionError("无法在资源管理器中显示", e);
    }
  };

  const openConversation = () => {
    if (!conversationId) return;
    navigate(`/conversations/${conversationId}`);
  };

  /** 版本 / 软删区在右侧详情区开成一个标签页（与打开文件同一个出口）。 */
  const openPanel = (path: string) =>
    onOpenFile(path, workspacePanelTabName(path, ws.name));

  /** Inline rename, for both flavours of root: a `conv:` scratch renames the
   * conversation, a `folder:` row renames the folder itself (§5.4 用户起名). */
  const canRename = !!conversationId || (!!folderId && folderOwner);

  const startEdit = () => {
    if (!canRename) return;
    setDraft(ws.name);
    setEditing(true);
  };

  const commitEdit = () => {
    justCreatedFolderRef.current = false;
    setEditing(false);
    const name = draft.trim();
    if (!name || name === ws.name) return;
    if (conversationId) {
      renameMutation.mutate(
        { id: conversationId, title: name },
        { onError: (err) => notifyError(err, "重命名失败") },
      );
    } else if (folderId) {
      renameFolderMutation.mutate(
        { id: folderId, patch: { name } },
        { onError: (err) => notifyError(err, "重命名文件夹失败") },
      );
    }
  };

  const cancelEdit = () => {
    skipBlurRef.current = true;
    setEditing(false);
    const created = justCreatedFolderRef.current;
    justCreatedFolderRef.current = false;
    if (
      !created ||
      !folder ||
      !isUntitledFolderName(folder.name) ||
      !canDeleteFolder
    ) {
      return;
    }
    void confirmDeleteFolder();
  };

  const confirmDeleteFolder = async () => {
    if (!folder) return;
    const name = folder.name;
    try {
      await deleteFolderMutation.mutateAsync(folder.id);
    } catch (err) {
      notifyError(err, "删除文件夹失败");
      return;
    }
    setDeleteFolderOpen(false);
    const leftActive = releaseFolderConversations(folder.id, {
      dropRuntime: dropConversationRuntime,
      locationId,
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

  const confirmPermanentDeleteFolder = () => {
    if (!folder) return;
    for (const { id } of memberConversations()) {
      dropConversationRuntime(id);
      if (id === locationId) navigate("/");
    }
    permanentDeleteFolderMutation.mutate(folder.id, {
      onSuccess: () => setDeleteFolderOpen(false),
      onError: (err) => notifyError(err, "彻底删除失败"),
    });
  };

  const handleDeleteConversation = async () => {
    if (!conversationId) return;
    const wasOnCanvas = conversationId === locationId;
    const title = ws.name;
    try {
      await deleteMutation.mutateAsync(conversationId);
    } catch (err) {
      notifyError(err, "删除失败");
      return;
    }
    dropConversationRuntime(conversationId);
    if (wasOnCanvas) navigate("/");
    notifyConversationDeleted(title, () =>
      restoreConversationMutation.mutate(conversationId),
    );
  };

  /** Cloud conv scratch only: wipe top-level entries; root itself stays. */
  const confirmClearScratch = async () => {
    if (!source || !conversationId || isLocal) return;
    if (scratchGenerating) {
      notifyError("对话进行中，无法清空产物");
      return;
    }
    setClearingScratch(true);
    try {
      const items = await source.listDir("");
      for (const item of items) {
        await source.delete(item.path);
      }
      // Empty cloud scratch drops off GET /v1/workspaces.
      removeConversationScratch(conversationId);
      await queryClient.invalidateQueries({ queryKey: workspaceKeys.list });
      treeRef.current?.refresh();
      setClearScratchOpen(false);
    } catch (e) {
      notifyActionError("清空失败", e);
      void queryClient.invalidateQueries({ queryKey: workspaceKeys.list });
    } finally {
      setClearingScratch(false);
    }
  };

  const header = editing ? (
    <div
      className={cn(
        "flex h-9 items-center rounded-lg px-2",
        flashing && "ring-2 ring-inset ring-primary",
      )}
    >
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            inputRef.current?.blur();
          } else if (e.key === "Escape") {
            e.preventDefault();
            cancelEdit();
          }
        }}
        onBlur={() => {
          if (skipBlurRef.current) {
            skipBlurRef.current = false;
            return;
          }
          commitEdit();
        }}
        style={{ marginLeft: depth * 12 }}
        className="h-7 min-w-0 flex-1 bg-transparent px-1 text-sm focus:outline-none"
        aria-label={folderId ? "重命名文件夹" : "重命名对话"}
      />
    </div>
  ) : (
    <div
      className={cn(
        "group flex items-center rounded-lg pr-1 text-sm",
        flashing && "ring-2 ring-inset ring-primary",
        offlineCloud && "opacity-60",
      )}
    >
      <Button
        variant="ghost"
        onClick={onToggle}
        aria-expanded={expanded}
        style={{ paddingLeft: 8 + depth * 12 }}
        className="h-auto min-h-9 min-w-0 flex-1 justify-start gap-1.5 overflow-hidden rounded-none py-1.5 pr-0 text-left text-sm font-medium"
      >
        {expanded ? (
          <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-muted-foreground" />
        )}
        {expanded ? (
          <FolderOpen size={14} className="shrink-0 text-muted-foreground" />
        ) : (
          <Folder size={14} className="shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 truncate font-medium">{ws.name}</span>
        {shareable && !folderOwner && (
          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-xs font-normal text-muted-foreground">
            {folderRoleLabel(folderRole)}
          </span>
        )}
      </Button>
      {folder && folderHasCollaborators(folder) && (
        <FolderCollabMark
          count={folder.collaboratorCount ?? 0}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setMembersOpen(true);
          }}
        />
      )}
      {source?.caps.edit && (
        <div className="hidden shrink-0 items-center group-hover:flex">
          <IconButton
            title="新建文件"
            onClick={() => requestTreeAction("file")}
          >
            <FilePlus size={14} />
          </IconButton>
          <IconButton
            title="新建子文件夹"
            onClick={() => requestTreeAction("dir")}
          >
            <FolderPlus size={14} />
          </IconButton>
        </div>
      )}
      {showLocationBadge && (
        <span
          className={`flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-xs ${
            isLocal
              ? "bg-primary/10 text-primary"
              : "bg-muted text-muted-foreground"
          }`}
        >
          {isLocal ? <HardDrive size={12} /> : <Cloud size={12} />}
          {isLocal ? "本地" : "云端"}
        </span>
      )}
    </div>
  );

  const hintStyle = { paddingLeft: 28 + depth * 12 };
  const tree = offlineCloud ? (
    <div className="py-1 text-xs text-muted-foreground" style={hintStyle}>
      离线时云端文件不可用；本机文件夹可浏览（只读），恢复连接后再改文件。
    </div>
  ) : localUnavailable ? (
    <div className="py-1 text-xs text-muted-foreground/70" style={hintStyle}>
      本机文件夹里的文件在你电脑上，请在桌面端查看。
    </div>
  ) : source ? (
    <FileTree
      ref={treeRef}
      source={source}
      chrome={false}
      indent={14 + depth * 12}
      activePath={activePath}
      filterQuery={filterQuery}
      sortBy={sortBy}
      hideRootDirs={hideRootDirs}
      renderWorkroomLead={renderWorkroomLead}
      onCreateWorkroomEntry={onCreateWorkroomEntry}
      forceExpandPaths={forceExpandWorkroom ? ["AgentCore"] : undefined}
      onForceExpandApplied={onWorkroomRevealApplied}
      onOpenFile={onOpenFile}
      emptyText="还没有文件——对话里 AI 产出的文件会落在这里"
    />
  ) : (
    <div className="py-1 text-xs text-muted-foreground/70" style={hintStyle}>
      无法打开此工作区，文件源暂不可用。
    </div>
  );

  return (
    <div ref={rootRef}>
      {editing ? (
        header
      ) : (
        <ContextMenu>
          <ContextMenuTrigger asChild>{header}</ContextMenuTrigger>
          <ContextMenuContent>
            {!localUnavailable && !offlineCloud && source?.caps.edit && (
              <>
                <ContextMenuItem onSelect={() => requestTreeAction("file")}>
                  <FilePlus size={14} className="shrink-0" />
                  <span className="flex-1 truncate">新建文件</span>
                </ContextMenuItem>
                <ContextMenuItem onSelect={() => requestTreeAction("dir")}>
                  <FolderPlus size={14} className="shrink-0" />
                  <span className="flex-1 truncate">新建子文件夹</span>
                </ContextMenuItem>
                {canNestCloudFolder && folder ? (
                  <ContextMenuItem
                    onSelect={() =>
                      useFoldersStore
                        .getState()
                        .requestUntitledCloudFolder(folder.id)
                    }
                  >
                    <FolderPlus size={14} className="shrink-0" />
                    <span className="flex-1 truncate">在此新建文件夹</span>
                  </ContextMenuItem>
                ) : null}
                {source.caps.transfer && (
                  <>
                    <ContextMenuItem
                      onSelect={() => requestTreeAction("upload")}
                    >
                      <Upload size={14} className="shrink-0" />
                      <span className="flex-1 truncate">上传文件</span>
                    </ContextMenuItem>
                    <ContextMenuItem
                      onSelect={() => requestTreeAction("upload-folder")}
                    >
                      <FolderUp size={14} className="shrink-0" />
                      <span className="flex-1 truncate">上传文件夹</span>
                    </ContextMenuItem>
                  </>
                )}
                {canClone && (
                  <ContextMenuItem onSelect={() => setCloneOpen(true)}>
                    <GitBranch size={14} className="shrink-0" />
                    <span className="flex-1 truncate">克隆仓库</span>
                  </ContextMenuItem>
                )}
                <ContextMenuSeparator />
              </>
            )}
            {canVersions && (
              <ContextMenuItem onSelect={() => openPanel(WS_VERSIONS_PATH)}>
                <History size={14} className="shrink-0" />
                <span className="flex-1 truncate">版本…</span>
              </ContextMenuItem>
            )}
            {canTrash && (
              <ContextMenuItem onSelect={() => openPanel(WS_TRASH_PATH)}>
                <Trash2 size={14} className="shrink-0" />
                <span className="flex-1 truncate">软删区…</span>
              </ContextMenuItem>
            )}
            {(canVersions || canTrash) && <ContextMenuSeparator />}
            {source?.revealInOsFileManager && (
              <>
                <ContextMenuItem onSelect={() => void revealRoot()}>
                  <FolderSearch size={14} className="shrink-0" />
                  <span className="flex-1 truncate">在资源管理器中显示</span>
                </ContextMenuItem>
                <ContextMenuSeparator />
              </>
            )}
            {conversationId && (
              <ContextMenuItem onSelect={openConversation}>
                <MessageSquare size={14} className="shrink-0" />
                <span className="flex-1 truncate">打开对话</span>
              </ContextMenuItem>
            )}
            {canClearScratch && (
              <ContextMenuItem
                variant="danger"
                disabled={scratchGenerating}
                onSelect={() => setClearScratchOpen(true)}
              >
                <Eraser size={14} className="shrink-0" />
                <span className="flex-1 truncate">
                  {scratchGenerating
                    ? "清空本对话产物（对话进行中）"
                    : "清空本对话产物…"}
                </span>
              </ContextMenuItem>
            )}
            {canRename && (
              <ContextMenuItem onSelect={startEdit}>
                <Pencil size={14} className="shrink-0" />
                <span className="flex-1 truncate">重命名</span>
              </ContextMenuItem>
            )}
            {shareable && (
              <ContextMenuItem onSelect={() => setMembersOpen(true)}>
                <Users size={14} className="shrink-0" />
                <span className="flex-1 truncate">成员</span>
              </ContextMenuItem>
            )}
            {conversationId && (
              <ContextMenuItem
                variant="danger"
                onSelect={() => void handleDeleteConversation()}
              >
                <Trash2 size={14} className="shrink-0" />
                <span className="flex-1 truncate">删除对话</span>
              </ContextMenuItem>
            )}
            {canDeleteFolder && (
              <>
                <ContextMenuSeparator />
                <ContextMenuItem
                  variant="danger"
                  onSelect={() => setDeleteFolderOpen(true)}
                >
                  <Trash2 size={14} className="shrink-0" />
                  <span className="flex-1 truncate">删除文件夹…</span>
                </ContextMenuItem>
              </>
            )}
          </ContextMenuContent>
        </ContextMenu>
      )}
      {expanded && (
        <>
          {nested}
          {tree}
        </>
      )}
      {shareable && folder && (
        <FolderMembersDialog
          open={membersOpen}
          onClose={() => setMembersOpen(false)}
          folderId={folder.id}
          folderName={folder.name}
          myRole={folderRole}
        />
      )}
      {canClearScratch && (
        <ConfirmDialog
          open={clearScratchOpen}
          onOpenChange={setClearScratchOpen}
          title={`清空「${ws.name}」的对话产物？`}
          description="将立刻删除本对话工作区下的全部文件，对话本身保留。云端产物清空后不可恢复。"
          confirmLabel="清空产物"
          tone="danger"
          busy={clearingScratch}
          onConfirm={() => void confirmClearScratch()}
        />
      )}
      {canDeleteFolder && folder && (
        <DeleteFolderDialog
          open={deleteFolderOpen}
          onOpenChange={setDeleteFolderOpen}
          name={folder.name}
          liveConvCount={memberConversations().length}
          isLocal={isLocal}
          onConfirm={() => void confirmDeleteFolder()}
          onPermanentConfirm={confirmPermanentDeleteFolder}
        />
      )}
      {canClone && (
        <CloneRepoDialog
          open={cloneOpen}
          onOpenChange={setCloneOpen}
          wsId={ws.wsId}
          onCloned={() => treeRef.current?.refresh()}
        />
      )}
    </div>
  );
}
