import {
  Badge,
  Button,
  Card,
  IconButton,
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
import { startNewConversation } from "@/lib/newConversation";
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
  Plus,
  Trash2,
} from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArchivedConversationManageRow } from "./ArchivedConversationManageRow";
import { CollaborationTimelinePanel } from "./CollaborationTimeline";
import { ConversationManageRow } from "./ConversationManageRow";
import { DeletedConversationManageRow } from "./DeletedConversationManageRow";
import { DeletedFolderManageRow } from "./DeletedFolderManageRow";
import {
  ALL_KEY,
  ARCHIVED_KEY,
  STALE_DAYS,
  TRASH_KEY,
  activeFilterName,
  filesFocusState,
  isRealFolderFilter,
  newChatFolderTarget,
} from "./constants";
import { folderAccentVar } from "./folderAccent";
import { groupConversationsByRecency } from "./groupByRecency";
import { useConversationBulkSelect } from "./useConversationBulkSelect";
import {
  useConversationList,
  useConversationRouting,
} from "./useConversationList";

/**
 * Dedicated conversation management page (`/conversations`). Timeline-style
 * dense list with view/project nav — sidebar only keeps recent chats.
 */
export function ConversationsPage() {
  const navigate = useNavigate();
  const { selected, setSelected, flashId, folderIds, folders } =
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
  } = useConversationList(selected, folderIds);
  const bulk = useConversationBulkSelect(list, selected, isArchivedView);

  const activeName = activeFilterName(selected, folders);
  const isFolderFilter = isRealFolderFilter(selected, folderIds);
  const showFolderTag = selected === ALL_KEY || selected === ARCHIVED_KEY;

  const groups = useMemo(() => groupConversationsByRecency(list), [list]);

  const handleNewChat = () => {
    startNewConversation(navigate, newChatFolderTarget(selected, folderIds));
  };

  return (
    <div className="h-full w-full overflow-hidden">
      <div className="mx-auto flex h-full max-w-[1400px] flex-col px-6 py-6">
        <header className="shrink-0">
          <h1 className="text-xl font-semibold text-foreground">全部对话</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            按时间浏览与管理对话，点击即可打开
          </p>
        </header>

        <div className="mt-5 flex min-h-0 flex-1 gap-5">
          <aside className="flex w-56 shrink-0 flex-col">
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
                    label="未分组"
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

              {folders.length > 0 && (
                <div>
                  <SectionLabel className="mb-1.5 px-2">文件夹</SectionLabel>
                  <div className="space-y-0.5">
                    {folders.map((f) => (
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
            <div className="flex shrink-0 items-center gap-3">
              <SearchField
                size="md"
                value={query}
                onValueChange={setQuery}
                placeholder={`在「${activeName}」中搜索…`}
                aria-label={`在「${activeName}」中搜索对话`}
                className="min-w-0 flex-1"
              />
              <Button
                className="h-9 shrink-0"
                icon={<Plus size={16} className="shrink-0" />}
                onClick={handleNewChat}
              >
                新建对话
              </Button>
            </div>

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
                <DropdownMenuContent align="start" className="min-w-40">
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
            </div>

            <div className="relative mt-3 min-h-0 flex-1 overflow-y-auto">
              {isFolderFilter && (
                <CollaborationTimelinePanel folderId={selected} />
              )}
              {isTrashView ? (
                <RecentlyDeletedPane
                  conversations={deletedConversationList}
                  folders={trashList}
                  searching={query.trim().length > 0}
                  retentionDays={retentionDays}
                />
              ) : list.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
                  <MessageSquare
                    size={28}
                    className="text-muted-foreground/40"
                  />
                  <p className="text-sm text-muted-foreground">
                    {query.trim()
                      ? "未找到匹配的对话"
                      : staleOnly
                        ? `暂无超过 ${STALE_DAYS} 天未活跃的对话`
                        : isArchivedView
                          ? "暂无已归档对话"
                          : conversations.length === 0
                            ? "暂无对话"
                            : "此文件夹暂无对话"}
                  </p>
                </div>
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
                    {bulk.confirmBulkDelete
                      ? `删除 ${bulk.selectedIds.size} 项？可在「最近删除」里恢复`
                      : `已选 ${bulk.selectedIds.size} 项`}
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
                  {bulk.confirmBulkDelete ? (
                    <>
                      <Button
                        variant="danger"
                        onClick={() => void bulk.handleBulkDelete()}
                      >
                        确认删除
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => bulk.setConfirmBulkDelete(false)}
                      >
                        取消
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="danger"
                      onClick={() => bulk.setConfirmBulkDelete(true)}
                      icon={<Trash2 size={14} className="shrink-0" />}
                    >
                      删除
                    </Button>
                  )}
                </Card>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

/**
 * 最近删除 pane — deleted conversations and deleted projects, neither of which is a
 * live `Conversation`, so no recency grouping (the server already returns each list
 * most-recently-deleted first) and no bulk bar. 彻底删除 is deliberately absent: that
 * lives behind the checkbox in the folder delete dialog, where the user asked for it.
 *
 * Each section states what its restore does *not* bring back. A recycle bin that
 * overstates its own reach is the thing this view exists to fix.
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
          <p className="px-1 pb-1 text-xs text-muted-foreground">
            恢复会把对话连同全部消息带回原来的位置。删除时已撤销的公开分享链接不会一起回来，需要重新分享；本机裸聊的工作目录在系统回收站里，从那里还原。
          </p>
          {conversations.map((c) => (
            <DeletedConversationManageRow key={c.id} conversation={c} />
          ))}
        </div>
      )}
      {folders.length > 0 && (
        <div className="space-y-1">
          <SectionLabel className="px-1">文件夹</SectionLabel>
          <p className="px-1 pb-1 text-xs text-muted-foreground">
            恢复会把文件夹和它一并归档的对话带回来；白板不会回到文件夹下，裸聊的自动云桌指针也不恢复（下回合自动重建）。
          </p>
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
