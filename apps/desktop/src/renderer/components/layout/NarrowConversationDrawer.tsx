import { ConversationItem } from "@/components/sidebar/ConversationItem";
import { PinnedConversations } from "@/components/sidebar/PinnedConversations";
import { WorkspaceGroupHeader } from "@/components/sidebar/WorkspaceGroupHeader";
import {
  FolderGroupDragGhost,
  FolderGroupInsertLine,
  folderGroupInsertPlace,
} from "@/components/sidebar/folderGroupDragFeedback";
import { useFolderGroupReorder } from "@/components/sidebar/useFolderGroupReorder";
import { IconButton, SurfaceRow, SurfaceRowButton } from "@/components/ui";
import {
  useConversationTrash,
  useConversations,
  useRestoreConversation,
} from "@/hooks/useConversations";
import { useFolders } from "@/hooks/useFolders";
import {
  buildWorkspaceGroups,
  foldersForConversationRail,
} from "@/hooks/useWorkspaceGroups";
import { deriveGroupWorkspaceIsLocal } from "@/lib/conversationWorkspaceMode";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import type { DeletedConversationMeta } from "@/services/conversations";
import { isSharedWithMeFolder } from "@/services/folders";
import { useRequiredConversationIds } from "@/stores/aiAttention";
import { useSidebarStore } from "@/stores/sidebar";
import {
  ArchiveRestore,
  ArrowLeft,
  MessageSquare,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

function byRecency(a: { updatedAt: string }, b: { updatedAt: string }): number {
  return (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0);
}

export function NarrowConversationDrawer() {
  const { isNarrow, conversationDrawerOpen, setConversationDrawerOpen } =
    useNarrowLayoutState();
  const conversations = useConversations();
  const folders = useFolders();
  const requiredIds = useRequiredConversationIds();
  const [view, setView] = useState<"live" | "trash">("live");
  const listedFolders = useMemo(
    () => foldersForConversationRail(folders),
    [folders],
  );
  const ownedFolders = useMemo(
    () => listedFolders.filter((f) => !isSharedWithMeFolder(f)),
    [listedFolders],
  );
  const sharedFolders = useMemo(
    () => listedFolders.filter(isSharedWithMeFolder),
    [listedFolders],
  );
  const trashQuery = useConversationTrash(
    isNarrow && conversationDrawerOpen && view === "trash",
  );
  const restoreMutation = useRestoreConversation();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const folderGroupOrder = useSidebarStore((s) => s.folderGroupOrder);

  const groups = useMemo(
    () =>
      buildWorkspaceGroups(conversations, ownedFolders, new Set(), {
        uncapped: true,
        folderGroupOrder,
      }),
    [conversations, ownedFolders, folderGroupOrder],
  );
  const sharedGroups = useMemo(
    () =>
      buildWorkspaceGroups(conversations, sharedFolders, new Set(), {
        uncapped: true,
        includeEmpty: true,
      }),
    [conversations, sharedFolders],
  );
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

  const bare = useMemo(
    () =>
      conversations
        .filter((c) => !c.folderId && !c.pinned && !c.archived)
        .sort(byRecency),
    [conversations],
  );

  useEffect(() => {
    if (!conversationDrawerOpen) setView("live");
  }, [conversationDrawerOpen]);

  if (!isNarrow || !conversationDrawerOpen) return null;

  const close = () => setConversationDrawerOpen(false);
  const trashItems = trashQuery.data?.items ?? [];

  return (
    <div className="absolute inset-0 z-40">
      <button
        type="button"
        className="absolute inset-0 bg-overlay"
        aria-label="关闭对话列表"
        onClick={close}
      />
      <aside
        className="absolute inset-y-0 left-0 flex w-[min(20rem,86vw)] flex-col bg-sidebar pt-[env(safe-area-inset-top)] text-sidebar-foreground shadow-md"
        style={{ backgroundImage: "var(--sidebar-gradient)" }}
        // biome-ignore lint/a11y/useSemanticElements: 自定义遮罩抽屉；原生 dialog 的 modal/form 语义不合适。
        role="dialog"
        aria-label="对话列表"
      >
        <div className="flex h-12 shrink-0 items-center justify-between px-2">
          {view === "trash" ? (
            <span className="flex min-w-0 flex-1 items-center">
              <IconButton
                size="md"
                aria-label="返回对话列表"
                onClick={() => setView("live")}
                tone="sidebar"
              >
                <ArrowLeft size={18} />
              </IconButton>
              <span className="min-w-0 truncate px-2 text-sm font-medium">
                最近删除
              </span>
            </span>
          ) : (
            <span className="px-2 text-sm font-medium">对话</span>
          )}
          <IconButton
            size="md"
            aria-label="关闭"
            onClick={close}
            tone="sidebar"
          >
            <X size={18} />
          </IconButton>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto pb-[env(safe-area-inset-bottom)]">
          {view === "trash" ? (
            <TrashView
              items={trashItems}
              loading={trashQuery.isLoading}
              restoring={restoreMutation.isPending}
              onRestore={(id) => restoreMutation.mutate(id)}
            />
          ) : (
            <>
              {conversations.length === 0 && sharedGroups.length === 0 ? (
                <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
                  <MessageSquare
                    size={24}
                    className="text-sidebar-foreground/30"
                  />
                  <p className="text-sm text-sidebar-foreground/50">暂无对话</p>
                </div>
              ) : (
                <>
                  <PinnedConversations onActivate={close} />
                  {groups.map(({ folder, convs }) => {
                    const visible = convs.filter(
                      (c) => !c.pinned && !c.archived,
                    );
                    const hasRequired = visible.some((c) =>
                      requiredIds.has(c.id),
                    );
                    const expanded =
                      hasRequired || collapsed[folder.id] !== true;
                    const insert = folderGroupInsertPlace(
                      folder.id,
                      draggingId,
                      overId,
                      place,
                    );
                    return (
                      <div key={folder.id}>
                        <div className="relative px-2 pt-2">
                          {insert ? (
                            <FolderGroupInsertLine place={insert} />
                          ) : null}
                          <WorkspaceGroupHeader
                            folder={folder}
                            convs={convs}
                            expanded={expanded}
                            surface="narrow"
                            sortable={getItemProps(folder.id)}
                            onToggleExpanded={() =>
                              setCollapsed((prev) => ({
                                ...prev,
                                [folder.id]: expanded,
                              }))
                            }
                          />
                        </div>
                        {expanded &&
                          visible.map((conv) => (
                            <div key={conv.id} className="px-2">
                              <ConversationItem
                                conversation={conv}
                                onActivate={close}
                              />
                            </div>
                          ))}
                      </div>
                    );
                  })}
                  {sharedGroups.length > 0 && (
                    <>
                      <div className="px-2 pb-0.5 pt-2 text-xs font-medium tracking-wide text-sidebar-foreground/40">
                        与我共享
                      </div>
                      {sharedGroups.map(({ folder, convs }) => {
                        const visible = convs.filter(
                          (c) => !c.pinned && !c.archived,
                        );
                        const hasRequired = visible.some((c) =>
                          requiredIds.has(c.id),
                        );
                        const expanded =
                          hasRequired || collapsed[folder.id] !== true;
                        return (
                          <div key={folder.id}>
                            <div className="px-2 pt-2">
                              <WorkspaceGroupHeader
                                folder={folder}
                                convs={convs}
                                expanded={expanded}
                                surface="narrow"
                                onToggleExpanded={() =>
                                  setCollapsed((prev) => ({
                                    ...prev,
                                    [folder.id]: expanded,
                                  }))
                                }
                              />
                            </div>
                            {expanded &&
                              visible.map((conv) => (
                                <div key={conv.id} className="px-2">
                                  <ConversationItem
                                    conversation={conv}
                                    onActivate={close}
                                  />
                                </div>
                              ))}
                          </div>
                        );
                      })}
                    </>
                  )}
                  {bare.length > 0 && (
                    <div className="space-y-0.5 px-2 py-1">
                      {bare.map((conv) => (
                        <ConversationItem
                          key={conv.id}
                          conversation={conv}
                          onActivate={close}
                        />
                      ))}
                    </div>
                  )}
                  {dragPreview && dragFolder ? (
                    <FolderGroupDragGhost
                      label={dragFolder.name}
                      isLocal={deriveGroupWorkspaceIsLocal(dragFolder)}
                      preview={dragPreview}
                    />
                  ) : null}
                </>
              )}
              <div className="mt-2 border-t border-sidebar-border px-2 py-1">
                <SurfaceRowButton
                  className="h-8 px-2 text-sidebar-foreground/70"
                  onClick={() => setView("trash")}
                >
                  <Trash2 size={14} className="shrink-0" />
                  最近删除
                </SurfaceRowButton>
              </div>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

function TrashView({
  items,
  loading,
  restoring,
  onRestore,
}: {
  items: DeletedConversationMeta[];
  loading: boolean;
  restoring: boolean;
  onRestore: (id: string) => void;
}) {
  if (loading && items.length === 0) {
    return (
      <p className="px-4 py-8 text-center text-sm text-sidebar-foreground/50">
        加载中…
      </p>
    );
  }
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
        <Trash2 size={24} className="text-sidebar-foreground/30" />
        <p className="text-sm text-sidebar-foreground/50">最近删除是空的</p>
      </div>
    );
  }
  return (
    <div className="space-y-0.5 px-2 py-1">
      {items.map((item) => (
        <SurfaceRow key={item.id} variant="sidebar" className="h-8 px-2">
          <span className="min-w-0 flex-1 truncate">{item.title}</span>
          <IconButton
            tone="sidebar"
            aria-label={`恢复对话 ${item.title}`}
            disabled={restoring}
            onClick={() => onRestore(item.id)}
          >
            <ArchiveRestore size={13} />
          </IconButton>
        </SurfaceRow>
      ))}
    </div>
  );
}
