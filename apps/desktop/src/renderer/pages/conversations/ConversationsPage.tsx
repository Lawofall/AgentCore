import { NarrowMenuButton } from "@/components/layout/NarrowMenuButton";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyHint,
  IconButton,
  PageHeader,
  SearchField,
  SectionLabel,
  SurfaceRowButton,
} from "@/components/ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { folderAncestorNames } from "@/lib/folderTree";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { cn } from "@/lib/utils";
import type { DeletedConversationMeta } from "@/services/conversations";
import type { DeletedFolderMeta, FolderMeta } from "@/services/folders";
import { UNGROUPED_KEY } from "@/stores/folders";
import {
  Archive,
  ArchiveRestore,
  ArrowDownWideNarrow,
  Check,
  CheckSquare,
  FolderOpen,
  Inbox,
  ListChecks,
  MessageSquare,
  Trash2,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ArchivedConversationManageRow } from "./ArchivedConversationManageRow";
import { ConversationManageRow } from "./ConversationManageRow";
import { DeletedConversationManageRow } from "./DeletedConversationManageRow";
import { DeletedFolderManageRow } from "./DeletedFolderManageRow";
import {
  ALL_KEY,
  ARCHIVED_KEY,
  STALE_DAYS,
  TRASH_KEY,
  activeFilterName,
  emptyTrashConfirmCopy,
  filesFocusState,
  isRealFolderFilter,
} from "./constants";
import { folderAccentVar } from "./folderAccent";
import { groupConversationsByRecency } from "./groupByRecency";
import {
  foldersWithLiveChats,
  resolveFolderFilterSelection,
} from "./liveFolderCounts";
import { useConversationBulkSelect } from "./useConversationBulkSelect";
import {
  useConversationList,
  useConversationRouting,
} from "./useConversationList";
import { useEmptyRecentlyDeleted } from "./useEmptyRecentlyDeleted";

/**
 * Dedicated conversation management page (`/conversations`). Timeline-style
 * dense list with view/project nav — sidebar only keeps recent chats.
 */
export function ConversationsPage() {
  const { isNarrow } = useNarrowLayoutState();
  const navigate = useNavigate();
  const location = useLocation();
  const consumedFolderJump = useRef<string | null>(null);
  const { selected, setSelected, flashId, folderIds, folders, foldersAll } =
    useConversationRouting();
  const {
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
    conversationTrashTotal = 0,
    folderTrashTotal = 0,
    conversationTrashListed = 0,
    folderTrashListed = 0,
    groupedSettled,
  } = useConversationList(selected, foldersAll);
  const bulk = useConversationBulkSelect(list, selected, isArchivedView);
  const emptyTrash = useEmptyRecentlyDeleted();
  const [emptyOpen, setEmptyOpen] = useState(false);

  const activeName = activeFilterName(selected, folders);
  const isFolderFilter = isRealFolderFilter(selected, folderIds);
  const showFolderTag = selected === ALL_KEY || selected === ARCHIVED_KEY;

  const groups = useMemo(() => groupConversationsByRecency(list), [list]);

  // The folder block is a conversation filter. A 0 badge filters to an empty
  // list, so those rows stay off the rail (files page still lists the folder).
  // Wait until the grouped cache has landed — an empty cache is not "no chats".
  const railReady = groupedSettled !== false;
  const visibleFolders = useMemo(
    () => (railReady ? foldersWithLiveChats(folders, counts.perFolder) : []),
    [railReady, folders, counts],
  );

  useEffect(() => {
    if (!railReady) return;
    const jump = (location.state as { focusFolderId?: string } | null)
      ?.focusFolderId;
    // A search/deep link onto a folder with no live chats is not a filter.
    // Consume the navigation once so a later archive of the last chat
    // returns here to 全部对话 instead of leaving for the files page.
    if (jump && consumedFolderJump.current !== location.key) {
      const shown = counts.canonical?.get(jump) ?? jump;
      if ((counts.perFolder.get(shown) ?? 0) === 0) {
        consumedFolderJump.current = location.key;
        const files = filesFocusState(shown);
        if (files) {
          navigate("/files", { replace: true, ...files });
          return;
        }
      } else {
        consumedFolderJump.current = location.key;
      }
    }
    const next = resolveFolderFilterSelection(
      selected,
      folderIds,
      counts.perFolder,
      counts.canonical,
    );
    if (next) setSelected(next);
  }, [railReady, selected, folderIds, counts, setSelected, location, navigate]);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      {isNarrow && (
        <header className="flex h-12 shrink-0 items-center gap-1 border-b border-border bg-card px-2 pt-[env(safe-area-inset-top)]">
          <NarrowMenuButton />
          <h1 className="min-w-0 flex-1 truncate text-center text-sm font-medium">
            全部对话
          </h1>
        </header>
      )}
      <div
        className={cn(
          "mx-auto flex min-h-0 w-full max-w-[1400px] flex-1 flex-col",
          isNarrow ? "px-4 py-4" : "px-6 py-6",
        )}
      >
        {!isNarrow && <PageHeader title="全部对话" className="shrink-0" />}

        <div
          className={cn(
            "flex min-h-0 flex-1",
            isNarrow ? "mt-0 flex-col gap-3" : "mt-5 gap-5",
          )}
        >
          <aside
            className={cn(
              "flex flex-col",
              isNarrow
                ? "max-h-40 w-full shrink-0 overflow-y-auto"
                : "w-56 shrink-0",
            )}
          >
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
              <div>
                <SectionLabel className="mb-1.5 px-2">视图</SectionLabel>
                <div className="space-y-0.5">
                  <FilterRow
                    icon={<MessageSquare size={16} />}
                    label="全部对话"
                    count={conversations.length}
                    selected={selected === ALL_KEY}
                    onSelect={() => setSelected(ALL_KEY)}
                  />
                  <FilterRow
                    icon={<Inbox size={16} />}
                    label="快速对话"
                    count={counts.ungrouped}
                    selected={selected === UNGROUPED_KEY}
                    onSelect={() => setSelected(UNGROUPED_KEY)}
                  />
                  <FilterRow
                    icon={<Archive size={16} />}
                    label="已归档"
                    count={archived.length}
                    selected={selected === ARCHIVED_KEY}
                    onSelect={() => setSelected(ARCHIVED_KEY)}
                  />
                  <FilterRow
                    icon={<Trash2 size={16} />}
                    label="最近删除"
                    count={trashCount}
                    selected={selected === TRASH_KEY}
                    onSelect={() => setSelected(TRASH_KEY)}
                  />
                </div>
              </div>

              {visibleFolders.length > 0 && (
                <div>
                  <SectionLabel className="mb-1.5 px-2">文件夹</SectionLabel>
                  <div className="space-y-0.5">
                    {visibleFolders.map((f) => (
                      <FolderFilterRow
                        key={f.id}
                        folder={f}
                        count={counts.perFolder.get(f.id) ?? 0}
                        selected={selected === f.id}
                        flashing={flashId === f.id}
                        onSelect={() => setSelected(f.id)}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          </aside>

          <section className="flex min-h-0 flex-1 flex-col">
            <SearchField
              size="md"
              value={query}
              onValueChange={setQuery}
              placeholder={`在「${activeName}」中搜索…`}
              aria-label={`在「${activeName}」中搜索对话`}
              className="w-full shrink-0"
            />

            <div className="mt-2.5 flex shrink-0 flex-wrap items-center gap-1.5">
              {!isArchivedView && !isTrashView && (
                <button
                  type="button"
                  onClick={() => setStaleOnly((v) => !v)}
                  className={`inline-flex h-7 items-center rounded-full border px-2.5 text-xs transition-colors ${
                    staleOnly
                      ? "border-primary/30 bg-primary/10 text-primary"
                      : "border-border bg-muted/40 text-muted-foreground hover:bg-accent hover:text-foreground"
                  }`}
                >
                  {STALE_DAYS} 天未活跃
                </button>
              )}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="inline-flex h-7 items-center gap-1 rounded-full border border-border bg-muted/40 px-2.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <ArrowDownWideNarrow size={12} className="shrink-0" />
                    最近优先
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start">
                  <DropdownMenuItem disabled>
                    <Check size={14} className="shrink-0 text-primary" />
                    <span className="flex-1">最近优先</span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              {!isTrashView && (
                <SimpleTooltip
                  label={bulk.selectMode ? "退出选择" : "批量选择"}
                >
                  <IconButton
                    aria-label={bulk.selectMode ? "退出选择" : "批量选择"}
                    className={`size-7 ${
                      bulk.selectMode
                        ? "bg-primary/10 text-primary"
                        : "text-muted-foreground"
                    }`}
                    onClick={() =>
                      bulk.selectMode
                        ? bulk.exitSelectMode()
                        : bulk.setSelectMode(true)
                    }
                  >
                    <ListChecks size={14} />
                  </IconButton>
                </SimpleTooltip>
              )}
              {bulk.selectMode && list.length > 0 && (
                <button
                  type="button"
                  onClick={bulk.toggleSelectAll}
                  className="inline-flex h-7 items-center gap-1.5 rounded-full border border-border px-2.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  {bulk.allVisibleSelected ? (
                    <CheckSquare size={12} className="shrink-0" />
                  ) : (
                    <span className="flex size-3 shrink-0 items-center justify-center rounded border border-border" />
                  )}
                  {bulk.allVisibleSelected ? "取消全选" : "全选"}
                </button>
              )}
              {isFolderFilter && (
                <button
                  type="button"
                  onClick={() => navigate("/files", filesFocusState(selected))}
                  className="ml-auto inline-flex h-7 items-center gap-1 rounded-full border border-border px-2.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <FolderOpen size={12} className="shrink-0" />
                  浏览文件
                </button>
              )}
              {isTrashView && trashCount > 0 && (
                <Button
                  variant="danger"
                  className="ml-auto"
                  icon={<Trash2 size={12} className="shrink-0" />}
                  onClick={() => setEmptyOpen(true)}
                >
                  清空
                </Button>
              )}
            </div>

            <div className="relative mt-3 min-h-0 flex-1 overflow-y-auto">
              {isTrashView ? (
                <RecentlyDeletedPane
                  conversations={deletedConversationList}
                  folders={trashList}
                  searching={query.trim().length > 0}
                  retentionDays={retentionDays}
                />
              ) : list.length === 0 ? (
                <EmptyHint
                  className="py-16"
                  icon={
                    <MessageSquare
                      size={28}
                      className="text-muted-foreground/40"
                    />
                  }
                  title={
                    query.trim()
                      ? "未找到匹配的对话"
                      : staleOnly
                        ? `暂无超过 ${STALE_DAYS} 天未活跃的对话`
                        : isArchivedView
                          ? "暂无已归档对话"
                          : conversations.length === 0
                            ? "暂无对话"
                            : "此文件夹暂无对话"
                  }
                />
              ) : (
                <div className="space-y-4 pb-4">
                  {groups.map((group) => (
                    <div key={group.id}>
                      <div className="sticky top-0 z-10 mb-1 flex items-center gap-2 bg-background/95 px-1 py-1 backdrop-blur-sm">
                        <SectionLabel>{group.label}</SectionLabel>
                        <span className="text-xs text-muted-foreground/50 tabular-nums">
                          {group.items.length}
                        </span>
                        <div className="h-px flex-1 bg-border/60" />
                      </div>
                      <div className="space-y-1">
                        {group.items.map((c) => (
                          <SelectableRow
                            key={c.id}
                            selectMode={bulk.selectMode}
                            selected={bulk.selectedIds.has(c.id)}
                            onToggle={() => bulk.toggleSelected(c.id)}
                          >
                            {isArchivedView ? (
                              <ArchivedConversationManageRow
                                conversation={c}
                                showFolderTag={showFolderTag}
                              />
                            ) : (
                              <ConversationManageRow
                                conversation={c}
                                showFolderTag={showFolderTag}
                              />
                            )}
                          </SelectableRow>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {bulk.selectMode && bulk.selectedIds.size > 0 && (
                <Card className="sticky bottom-0 mt-3 flex flex-wrap items-center gap-2 px-3 py-2 shadow-sm">
                  <span className="text-sm text-muted-foreground">
                    已选 {bulk.selectedIds.size} 项
                  </span>
                  <span className="flex-1" />
                  {isArchivedView ? (
                    <Button
                      variant="neutral"
                      onClick={bulk.handleBulkUnarchive}
                      icon={<ArchiveRestore size={14} className="shrink-0" />}
                    >
                      取消归档
                    </Button>
                  ) : (
                    <Button
                      variant="neutral"
                      onClick={() => void bulk.handleBulkArchive()}
                      icon={<Archive size={14} className="shrink-0" />}
                    >
                      批量归档
                    </Button>
                  )}
                  <Button
                    variant="danger"
                    onClick={() => void bulk.handleBulkDelete()}
                    icon={<Trash2 size={14} className="shrink-0" />}
                  >
                    删除
                  </Button>
                </Card>
              )}
            </div>
          </section>
        </div>
      </div>
      <ConfirmDialog
        open={emptyOpen}
        onOpenChange={setEmptyOpen}
        title="清空最近删除？"
        description={emptyTrashConfirmCopy({
          conversations: conversationTrashTotal,
          folders: folderTrashTotal,
          searching: query.trim().length > 0,
          listedConversations: conversationTrashListed,
          listedFolders: folderTrashListed,
        })}
        confirmLabel="清空"
        tone="danger"
        busy={emptyTrash.isPending}
        onConfirm={() => {
          emptyTrash.mutate(undefined, {
            onSuccess: () => setEmptyOpen(false),
          });
        }}
      />
    </div>
  );
}

/**
 * 最近删除 pane — deleted conversations and deleted projects, neither of which is a
 * live `Conversation`, so no recency grouping (the server already returns each list
 * most-recently-deleted first) and no per-row bulk bar. 彻底删除 stays per-row.
 * 清空 is the page button: one confirm, then both halves of the retention window.
 * Folder wipe matches the delete-dialog checkbox (chats + cloud files + desk
 * settings), via the tombstone path.
 */
function RecentlyDeletedPane({
  conversations,
  folders,
  searching,
  retentionDays,
}: {
  conversations: DeletedConversationMeta[];
  folders: DeletedFolderMeta[];
  searching: boolean;
  retentionDays: number | null;
}) {
  if (conversations.length === 0 && folders.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
        <Trash2 size={28} className="text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">
          {searching ? "未找到匹配的对话或文件夹" : "最近删除是空的"}
        </p>
        {!searching && retentionDays !== null && (
          <p className="text-xs text-muted-foreground/70">
            删除的对话和文件夹会在这里保留 {retentionDays} 天，其间随时可以恢复
          </p>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-4 pb-4">
      {conversations.length > 0 && (
        <div className="space-y-1">
          <SectionLabel className="px-1">对话</SectionLabel>
          {conversations.map((c) => (
            <DeletedConversationManageRow key={c.id} conversation={c} />
          ))}
        </div>
      )}
      {folders.length > 0 && (
        <div className="space-y-1">
          <SectionLabel className="px-1">文件夹</SectionLabel>
          {folders.map((f) => (
            <DeletedFolderManageRow key={f.id} folder={f} />
          ))}
        </div>
      )}
    </div>
  );
}

function SelectableRow({
  selectMode,
  selected,
  onToggle,
  children,
}: {
  selectMode: boolean;
  selected: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  if (!selectMode) return <>{children}</>;
  return (
    <div className="flex items-stretch gap-2">
      <div className="flex items-center pl-1">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label="选择对话"
          className="size-4 shrink-0 rounded border-border accent-primary"
        />
      </div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function FilterRow({
  icon,
  label,
  count,
  selected,
  onSelect,
}: {
  icon: ReactNode;
  label: string;
  count: number;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <SurfaceRowButton
      variant="default"
      onClick={onSelect}
      className={`h-9 w-full items-center gap-2 px-2 ${
        selected
          ? "bg-accent text-accent-foreground"
          : "text-foreground/70 hover:bg-accent/60 hover:text-foreground"
      }`}
    >
      <span
        className={`shrink-0 ${selected ? "text-foreground" : "text-muted-foreground"}`}
      >
        {icon}
      </span>
      <span className="flex-1 truncate text-left text-sm">{label}</span>
      <Badge
        tone={selected ? "primary" : "muted"}
        pill
        className="min-w-5 justify-center tabular-nums"
      >
        {count}
      </Badge>
    </SurfaceRowButton>
  );
}

function FolderFilterRow({
  folder,
  count,
  selected,
  flashing,
  onSelect,
}: {
  folder: FolderMeta;
  count: number;
  selected: boolean;
  flashing: boolean;
  onSelect: () => void;
}) {
  const navigate = useNavigate();
  const [hovered, setHovered] = useState(false);
  const accent = folderAccentVar(folder.id);
  /** 「设计 / 图标」— the filter list is flat, so nested folders need their path. */
  const ancestorLabel = folderAncestorNames(folder).join(" / ");

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={`group flex h-9 items-center gap-1 rounded-lg px-2 transition-shadow ${
        selected
          ? "bg-accent text-accent-foreground"
          : "text-foreground/70 hover:bg-accent/60 hover:text-foreground"
      } ${flashing ? "ring-2 ring-inset ring-primary" : ""}`}
    >
      <SurfaceRowButton
        variant="default"
        onClick={onSelect}
        className="min-w-0 flex-1 justify-start gap-2 bg-transparent px-0 text-inherit hover:bg-transparent"
      >
        <span
          className="size-2 shrink-0 rounded-full"
          style={{ backgroundColor: accent }}
          aria-hidden
        />
        <span className="min-w-0 flex-1 truncate text-left text-sm">
          {folder.name}
        </span>
        {ancestorLabel && (
          <span className="shrink-0 truncate text-xs text-muted-foreground/60">
            {ancestorLabel}
          </span>
        )}
      </SurfaceRowButton>
      {hovered ? (
        <SimpleTooltip label="浏览文件">
          <IconButton
            aria-label="浏览此文件夹的文件"
            onClick={() => navigate("/files", filesFocusState(folder.id))}
            className="size-6 shrink-0"
          >
            <FolderOpen size={13} />
          </IconButton>
        </SimpleTooltip>
      ) : (
        <Badge
          tone={selected ? "primary" : "muted"}
          pill
          className="min-w-5 justify-center tabular-nums"
        >
          {count}
        </Badge>
      )}
    </div>
  );
}
