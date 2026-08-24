import { Badge, IconButton, Input, SurfaceRow } from "@/components/ui";
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
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  useArchiveConversation,
  useDeleteConversation,
  useDuplicateConversation,
  useRenameConversation,
  useRestoreConversation,
  useTogglePin,
  useUnarchiveConversation,
} from "@/hooks/useConversations";
import { useFolders } from "@/hooks/useFolders";
import {
  DELETE_CONVERSATION_LABEL,
  deleteConversationConfirmLabel,
  notifyConversationDeleted,
} from "@/lib/conversationDeleteCopy";
import { timeAgo } from "@/lib/format";
import { notifyError, notifyInfo } from "@/lib/toast";
import {
  type ExportFormat,
  exportConversation,
} from "@/services/conversations";
import { useConversationAwaitingAttention } from "@/stores/aiAttention";
import {
  type Conversation,
  useConversationGenerating,
  useConversationStore,
} from "@/stores/conversation";
import {
  isAwaitingUserEntry,
  isRetiredKickoffKind,
  useInteractionStore,
} from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import { useShareStore } from "@/stores/share";
import {
  Archive,
  Check,
  Copy,
  Download,
  FileJson,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Share2,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { folderAccentVar } from "./folderAccent";

type Props = {
  conversation: Conversation;
  /** When false, hide the project color tag (already scoped to that folder). */
  showFolderTag?: boolean;
};

/**
 * Management-page conversation row — SurfaceRow family (same chrome as sidebar),
 * taller for title + preview. Reuses sidebar mutation hooks / menus.
 */
export function ConversationManageRow({
  conversation,
  showFolderTag = true,
}: Props) {
  const [hovered, setHovered] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [draft, setDraft] = useState(conversation.title);
  const inputRef = useRef<HTMLInputElement>(null);
  const skipBlurRef = useRef(false);

  const navigate = useNavigate();
  const currentId = useConversationStore((s) => s.currentConversationId);
  const switchConversation = useConversationStore((s) => s.switchConversation);
  const dropConversationRuntime = useConversationStore(
    (s) => s.dropConversationRuntime,
  );

  const renameMutation = useRenameConversation();
  const deleteMutation = useDeleteConversation();
  const restoreMutation = useRestoreConversation();
  const pinMutation = useTogglePin();
  const duplicateMutation = useDuplicateConversation();
  const archiveMutation = useArchiveConversation();
  const unarchiveMutation = useUnarchiveConversation();
  const folders = useFolders();

  const isGenerating = useConversationGenerating(conversation.id);
  const awaitingInteraction = useInteractionStore((s) =>
    [...s.byId.values()].some(
      (e) => e.conversationId === conversation.id && isAwaitingUserEntry(e),
    ),
  );
  const awaitingResume = usePausedTurnStore((s) =>
    s.pending.some(
      (p) =>
        p.conversationId === conversation.id && !isRetiredKickoffKind(p.kind),
    ),
  );
  // firehose `ai_attention`：另一端起的回合也亮灯（本端从未流过该对话时唯一的来源）。
  const awaitingAttention = useConversationAwaitingAttention(conversation.id);

  const status: "running" | "awaiting" | null =
    awaitingInteraction || awaitingResume || awaitingAttention
      ? "awaiting"
      : isGenerating
        ? "running"
        : null;

  const folder =
    conversation.folderId != null
      ? (folders.find((f) => f.id === conversation.folderId) ?? null)
      : null;
  const showActions = hovered || confirmingDelete || moreOpen;
  const preview = conversation.lastMessagePreview?.replace(/\s+/g, " ").trim();
  const relative = timeAgo(conversation.updatedAt);
  const deleteConfirmLabel = deleteConversationConfirmLabel(
    conversation.folderId ? "folder" : undefined,
  );

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const openConversation = () => {
    switchConversation(conversation.id);
    navigate(`/conversations/${conversation.id}`);
  };

  const startEdit = () => {
    setConfirmingDelete(false);
    setDraft(conversation.title);
    setEditing(true);
  };

  const commitEdit = () => {
    setEditing(false);
    const title = draft.trim();
    if (!title || title === conversation.title) return;
    renameMutation.mutate(
      { id: conversation.id, title },
      { onError: (err) => notifyError(err, "重命名失败") },
    );
  };

  const togglePin = () => {
    pinMutation.mutate(
      { id: conversation.id, pinned: !conversation.pinned },
      { onError: (err) => notifyError(err, "操作失败") },
    );
  };

  const handleArchive = async () => {
    const wasActive = conversation.id === currentId;
    const title = conversation.title;
    try {
      await archiveMutation.mutateAsync(conversation.id);
    } catch (err) {
      notifyError(err, "归档失败");
      return;
    }
    dropConversationRuntime(conversation.id);
    if (wasActive) navigate("/");
    notifyInfo("已归档", {
      description: title,
      duration: 5000,
      action: {
        label: "撤销",
        onClick: () => {
          unarchiveMutation.mutate(conversation.id, {
            onError: (err) => notifyError(err, "取消归档失败"),
          });
        },
      },
    });
  };

  const handleDelete = async () => {
    setConfirmingDelete(false);
    const wasActive = conversation.id === currentId;
    const title = conversation.title;
    try {
      await deleteMutation.mutateAsync(conversation.id);
    } catch (err) {
      notifyError(err, "删除失败");
      return;
    }
    dropConversationRuntime(conversation.id);
    if (wasActive) navigate("/");
    notifyConversationDeleted(title, () =>
      restoreMutation.mutate(conversation.id),
    );
  };

  const handleDuplicate = () => {
    setMoreOpen(false);
    duplicateMutation.mutate(conversation.id, {
      onSuccess: (conv) => {
        switchConversation(conv.id);
        navigate(`/conversations/${conv.id}`);
      },
      onError: (err) => notifyError(err, "克隆失败"),
    });
  };

  const handleExport = async (format: ExportFormat) => {
    try {
      await exportConversation(conversation.id, format);
    } catch (err) {
      notifyError(err, "导出失败");
    }
  };

  if (editing) {
    return (
      <SurfaceRow className="min-h-14 gap-2 bg-accent/40 px-3">
        <Input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              inputRef.current?.blur();
            } else if (e.key === "Escape") {
              e.preventDefault();
              skipBlurRef.current = true;
              setEditing(false);
            }
          }}
          onBlur={() => {
            if (skipBlurRef.current) {
              skipBlurRef.current = false;
              return;
            }
            commitEdit();
          }}
          className="min-w-0 flex-1 border-0 bg-transparent px-1 font-semibold shadow-none focus:border-transparent focus:ring-0"
        />
      </SurfaceRow>
    );
  }

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <SurfaceRow
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => {
            setHovered(false);
            if (!moreOpen) setConfirmingDelete(false);
          }}
          className="group relative min-h-14 items-stretch gap-3 px-3 py-2.5 hover:bg-accent/60"
        >
          {/* biome-ignore lint/a11y/useSemanticElements: 行内有 DropdownMenuTrigger 真 button，可点击区不可再套 button。 */}
          <div
            role="button"
            tabIndex={0}
            className="flex min-w-0 flex-1 items-start gap-2.5 text-left"
            onClick={openConversation}
            onDoubleClick={(e) => {
              e.preventDefault();
              startEdit();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                openConversation();
              }
            }}
          >
            {status ? (
              <span className="mt-1.5 flex size-4 shrink-0 items-center justify-center">
                <SimpleTooltip
                  label={status === "running" ? "执行中" : "等你决策"}
                >
                  <span
                    aria-label={status === "running" ? "执行中" : "等你决策"}
                    className={`size-2 rounded-full ${
                      status === "running"
                        ? "animate-pulse bg-primary"
                        : "bg-primary ring-2 ring-primary/25"
                    }`}
                  />
                </SimpleTooltip>
              </span>
            ) : null}

            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-2">
                <span className="min-w-0 truncate text-sm font-semibold text-foreground">
                  {conversation.title}
                </span>
                {conversation.pinned && !showActions && (
                  <Pin
                    size={12}
                    className="shrink-0 text-muted-foreground"
                    aria-label="已置顶"
                  />
                )}
                {showFolderTag && folder && (
                  <span
                    className="inline-flex max-w-[8rem] shrink-0 items-center gap-1 truncate rounded-lg border border-border bg-muted/40 px-1.5 py-0.5 text-xs text-muted-foreground"
                    title={folder.name}
                  >
                    <span
                      className="size-1.5 shrink-0 rounded-full"
                      style={{ backgroundColor: folderAccentVar(folder.id) }}
                    />
                    <span className="truncate">{folder.name}</span>
                  </span>
                )}
              </div>
              {preview ? (
                <p className="mt-0.5 truncate text-xs text-muted-foreground">
                  {preview}
                </p>
              ) : (
                <p className="mt-0.5 truncate text-xs text-muted-foreground/50">
                  暂无消息预览
                </p>
              )}
            </div>
          </div>

          <div className="flex shrink-0 flex-col items-end justify-between gap-1 py-0.5">
            <div className="flex h-6 items-center gap-1">
              {confirmingDelete ? (
                <span className="flex items-center gap-0.5">
                  <SimpleTooltip label={deleteConfirmLabel}>
                    <IconButton
                      aria-label={deleteConfirmLabel}
                      className="size-6 text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation();
                        void handleDelete();
                      }}
                    >
                      <Check size={13} />
                    </IconButton>
                  </SimpleTooltip>
                  <SimpleTooltip label="取消">
                    <IconButton
                      aria-label="取消"
                      className="size-6"
                      onClick={(e) => {
                        e.stopPropagation();
                        setConfirmingDelete(false);
                      }}
                    >
                      <X size={13} />
                    </IconButton>
                  </SimpleTooltip>
                </span>
              ) : showActions ? (
                <span className="flex items-center gap-0.5">
                  <SimpleTooltip
                    label={conversation.pinned ? "取消置顶" : "置顶"}
                  >
                    <IconButton
                      aria-label={conversation.pinned ? "取消置顶" : "置顶"}
                      className="size-6 text-muted-foreground hover:text-foreground"
                      onClick={(e) => {
                        e.stopPropagation();
                        togglePin();
                      }}
                    >
                      {conversation.pinned ? (
                        <PinOff size={13} />
                      ) : (
                        <Pin size={13} />
                      )}
                    </IconButton>
                  </SimpleTooltip>
                  <SimpleTooltip label="归档">
                    <IconButton
                      aria-label="归档"
                      className="size-6 text-muted-foreground hover:text-foreground"
                      onClick={(e) => {
                        e.stopPropagation();
                        void handleArchive();
                      }}
                    >
                      <Archive size={13} />
                    </IconButton>
                  </SimpleTooltip>
                  <DropdownMenu open={moreOpen} onOpenChange={setMoreOpen}>
                    <DropdownMenuTrigger asChild>
                      <IconButton
                        aria-label="更多操作"
                        title="更多"
                        className="size-6 text-muted-foreground hover:text-foreground"
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
                      <DropdownMenuItem onSelect={() => startEdit()}>
                        <Pencil size={14} className="shrink-0" />
                        <span className="flex-1 truncate">重命名</span>
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onSelect={handleDuplicate}>
                        <Copy size={14} className="shrink-0" />
                        <span className="flex-1 truncate">克隆对话</span>
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() =>
                          useShareStore.getState().open(conversation.id)
                        }
                      >
                        <Share2 size={14} className="shrink-0" />
                        <span className="flex-1 truncate">分享…</span>
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => void handleExport("md")}
                      >
                        <Download size={14} className="shrink-0" />
                        <span className="flex-1 truncate">导出 Markdown</span>
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => void handleExport("json")}
                      >
                        <FileJson size={14} className="shrink-0" />
                        <span className="flex-1 truncate">导出 JSON</span>
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant="danger"
                        onSelect={() => {
                          setMoreOpen(false);
                          setConfirmingDelete(true);
                        }}
                      >
                        <Trash2 size={14} className="shrink-0" />
                        <span className="flex-1 truncate">
                          {DELETE_CONVERSATION_LABEL}
                        </span>
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </span>
              ) : (
                <span className="text-xs text-muted-foreground tabular-nums">
                  {relative}
                </span>
              )}
            </div>
            <Badge tone="muted" pill className="tabular-nums">
              {conversation.messageCount} 条
            </Badge>
          </div>
        </SurfaceRow>
      </ContextMenuTrigger>

      <ContextMenuContent className="min-w-52">
        <ContextMenuItem onSelect={() => startEdit()}>
          <Pencil size={14} className="shrink-0" />
          <span className="flex-1 truncate">重命名</span>
        </ContextMenuItem>
        <ContextMenuItem onSelect={() => void handleArchive()}>
          <Archive size={14} className="shrink-0" />
          <span className="flex-1 truncate">归档</span>
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem onSelect={() => togglePin()}>
          {conversation.pinned ? (
            <PinOff size={14} className="shrink-0" />
          ) : (
            <Pin size={14} className="shrink-0" />
          )}
          <span className="flex-1 truncate">
            {conversation.pinned ? "取消置顶" : "置顶"}
          </span>
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem onSelect={handleDuplicate}>
          <Copy size={14} className="shrink-0" />
          <span className="flex-1 truncate">克隆对话</span>
        </ContextMenuItem>
        <ContextMenuItem
          onSelect={() => useShareStore.getState().open(conversation.id)}
        >
          <Share2 size={14} className="shrink-0" />
          <span className="flex-1 truncate">分享…</span>
        </ContextMenuItem>
        <ContextMenuItem onSelect={() => void handleExport("md")}>
          <Download size={14} className="shrink-0" />
          <span className="flex-1 truncate">导出 Markdown</span>
        </ContextMenuItem>
        <ContextMenuItem onSelect={() => void handleExport("json")}>
          <FileJson size={14} className="shrink-0" />
          <span className="flex-1 truncate">导出 JSON</span>
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          variant="danger"
          onSelect={() => setConfirmingDelete(true)}
        >
          <Trash2 size={14} className="shrink-0" />
          <span className="flex-1 truncate">{DELETE_CONVERSATION_LABEL}</span>
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}
