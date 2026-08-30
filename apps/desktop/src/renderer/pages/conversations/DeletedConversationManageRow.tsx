import { Badge, IconButton, SurfaceRow } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useRestoreConversation } from "@/hooks/useConversations";
import { useFolders } from "@/hooks/useFolders";
import { timeAgo } from "@/lib/format";
import type { DeletedConversationMeta } from "@/services/conversations";
import { ArchiveRestore } from "lucide-react";
import { retentionRemainingLabel } from "./constants";
import { folderAccentVar } from "./folderAccent";

/**
 * One row of「最近删除」— a deleted conversation waiting out its retention window.
 * Same density / chrome as {@link DeletedFolderManageRow}: the transcript cannot be
 * opened while it sits in the bin, so the row is inert apart from 恢复.
 *
 * A chat whose project was deleted too says so, because restoring it alone puts it in
 * 快速对话 rather than back under that project — the one case where「回到原来的位置」
 * would otherwise read as a broken promise.
 */
export function DeletedConversationManageRow({
  conversation,
}: {
  conversation: DeletedConversationMeta;
}) {
  const restoreMutation = useRestoreConversation();
  const folders = useFolders();
  const folder = conversation.folderId
    ? (folders.find((f) => f.id === conversation.folderId) ?? null)
    : null;
  const orphanedByFolder = !!conversation.folderId && folder === null;

  return (
    <SurfaceRow className="group relative min-h-14 items-stretch gap-3 px-3 py-2.5 hover:bg-accent/60">
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 truncate text-sm font-semibold text-foreground">
            {conversation.title}
          </span>
          {folder && (
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
          {orphanedByFolder && (
            <span className="shrink-0 rounded-lg border border-border bg-muted/40 px-1.5 py-0.5 text-xs text-muted-foreground">
              原文件夹也已删除
            </span>
          )}
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          删除于 {timeAgo(conversation.deletedAt)} · {conversation.messageCount}{" "}
          条消息
          {orphanedByFolder && " · 恢复后先回到快速对话"}
        </p>
      </div>

      <div className="flex shrink-0 flex-col items-end justify-between gap-1 py-0.5">
        <span className="flex h-6 items-center opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          <SimpleTooltip label="恢复到对话列表">
            <IconButton
              aria-label={`恢复对话 ${conversation.title}`}
              onClick={() => restoreMutation.mutate(conversation.id)}
              disabled={restoreMutation.isPending}
              className="size-6 text-muted-foreground hover:text-foreground"
            >
              <ArchiveRestore size={13} />
            </IconButton>
          </SimpleTooltip>
        </span>
        <Badge tone="muted" pill className="tabular-nums">
          {retentionRemainingLabel(conversation.purgeAt)}
        </Badge>
      </div>
    </SurfaceRow>
  );
}
