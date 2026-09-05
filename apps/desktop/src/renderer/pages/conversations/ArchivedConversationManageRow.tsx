import { Badge, IconButton, SurfaceRow } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  useDeleteConversation,
  useRestoreConversation,
  useUnarchiveConversation,
} from "@/hooks/useConversations";
import { useFolders } from "@/hooks/useFolders";
import {
  DELETE_CONVERSATION_LABEL,
  notifyConversationDeleted,
} from "@/lib/conversationDeleteCopy";
import { useConversationLocationId } from "@/lib/conversationLocation";
import { timeAgo } from "@/lib/format";
import { notifyError } from "@/lib/toast";
import type { Conversation } from "@/stores/conversation";
import { useConversationStore } from "@/stores/conversation";
import { ArchiveRestore, Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { folderAccentVar } from "./folderAccent";

type Props = {
  conversation: Conversation;
  showFolderTag?: boolean;
};

/** Archived row for the management page — same density as ConversationManageRow. */
export function ArchivedConversationManageRow({
  conversation,
  showFolderTag = true,
}: Props) {
  const navigate = useNavigate();
  const unarchiveMutation = useUnarchiveConversation();
  const deleteMutation = useDeleteConversation();
  const restoreMutation = useRestoreConversation();
  const switchConversation = useConversationStore((s) => s.switchConversation);
  const dropConversationRuntime = useConversationStore(
    (s) => s.dropConversationRuntime,
  );
  const locationId = useConversationLocationId();
  const folders = useFolders();
  const [hovered, setHovered] = useState(false);

  const folder =
    conversation.folderId != null
      ? (folders.find((f) => f.id === conversation.folderId) ?? null)
      : null;
  const preview = conversation.lastMessagePreview?.replace(/\s+/g, " ").trim();
  const relative = timeAgo(conversation.updatedAt);
  const showActions = hovered;

  const open = () => {
    switchConversation(conversation.id);
    navigate(`/conversations/${conversation.id}`);
  };

  const handleUnarchive = () => {
    unarchiveMutation.mutate(conversation.id, {
      onError: (err) => notifyError(err, "取消归档失败"),
    });
  };

  const handleDelete = async () => {
    const wasOnCanvas = conversation.id === locationId;
    const title = conversation.title;
    try {
      await deleteMutation.mutateAsync(conversation.id);
    } catch (err) {
      notifyError(err, "删除失败");
      return;
    }
    dropConversationRuntime(conversation.id);
    if (wasOnCanvas) navigate("/");
    // An archived chat restores straight back into 已归档 — the delete took it from
    // there, so the undo must not quietly promote it to the live list.
    notifyConversationDeleted(title, () =>
      restoreMutation.mutate(conversation.id),
    );
  };

  return (
    <SurfaceRow
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="group relative min-h-14 items-stretch gap-3 px-3 py-2.5 hover:bg-accent/60"
    >
      {/* biome-ignore lint/a11y/useSemanticElements: 行内有 IconButton，可点击区不可再套 button。 */}
      <div
        role="button"
        tabIndex={0}
        className="flex min-w-0 flex-1 items-start gap-2.5 text-left"
        onClick={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open();
          }
        }}
      >
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 truncate text-sm font-semibold text-foreground">
              {conversation.title}
            </span>
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
          {showActions ? (
            <span className="flex items-center gap-0.5">
              <SimpleTooltip label="取消归档">
                <IconButton
                  aria-label="取消归档"
                  onClick={handleUnarchive}
                  className="size-6 text-muted-foreground hover:text-foreground"
                >
                  <ArchiveRestore size={13} />
                </IconButton>
              </SimpleTooltip>
              <SimpleTooltip label={DELETE_CONVERSATION_LABEL}>
                <IconButton
                  aria-label={DELETE_CONVERSATION_LABEL}
                  onClick={() => void handleDelete()}
                  className="size-6 text-muted-foreground hover:text-destructive"
                >
                  <Trash2 size={13} />
                </IconButton>
              </SimpleTooltip>
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
  );
}
