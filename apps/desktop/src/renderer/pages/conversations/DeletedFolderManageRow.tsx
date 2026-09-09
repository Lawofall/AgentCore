import { Badge, ConfirmDialog, IconButton, SurfaceRow } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { usePurgeTrashedFolder, useRestoreFolder } from "@/hooks/useFolders";
import { timeAgo } from "@/lib/format";
import type { DeletedFolderMeta } from "@/services/folders";
import { ArchiveRestore, Cloud, HardDrive, Trash2 } from "lucide-react";
import { useState } from "react";
import { retentionRemainingLabel } from "./constants";
import { folderAccentVar } from "./folderAccent";

/**
 * One row of「最近删除」— a deleted folder waiting out its retention window.
 * Same density / chrome as {@link DeletedConversationManageRow}: a folder
 * cannot be opened while it sits in the bin, so the row is inert apart from
 * 恢复 / 彻底删除.
 */
export function DeletedFolderManageRow({
  folder,
}: {
  folder: DeletedFolderMeta;
}) {
  const restoreMutation = useRestoreFolder();
  const purgeMutation = usePurgeTrashedFolder();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const isLocal = folder.mode === "local";
  const busy = restoreMutation.isPending || purgeMutation.isPending;

  const handleRestore = () => {
    restoreMutation.mutate({ id: folder.id, name: folder.name });
  };

  return (
    <SurfaceRow className="group relative min-h-14 items-stretch gap-3 px-3 py-2.5 hover:bg-accent/60">
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className="size-2 shrink-0 rounded-full"
            style={{ backgroundColor: folderAccentVar(folder.id) }}
            aria-hidden
          />
          <span className="min-w-0 truncate text-sm font-semibold text-foreground">
            {folder.name}
          </span>
          <span className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-border bg-muted/40 px-1.5 py-0.5 text-xs text-muted-foreground">
            {isLocal ? <HardDrive size={11} /> : <Cloud size={11} />}
            {isLocal ? "本机" : "云端"}
          </span>
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          删除于 {timeAgo(folder.deletedAt)}
          {isLocal && " · 电脑上的文件夹未被改动"}
        </p>
      </div>

      <div className="flex shrink-0 flex-col items-end justify-between gap-1 py-0.5">
        <span className="flex h-6 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          <SimpleTooltip label="恢复到文件夹列表">
            <IconButton
              aria-label={`恢复文件夹 ${folder.name}`}
              onClick={handleRestore}
              disabled={busy}
              className="size-6 text-muted-foreground hover:text-foreground"
            >
              <ArchiveRestore size={13} />
            </IconButton>
          </SimpleTooltip>
          <SimpleTooltip label="彻底删除">
            <IconButton
              aria-label={`彻底删除文件夹 ${folder.name}`}
              onClick={() => setConfirmOpen(true)}
              disabled={busy}
              className="size-6 text-muted-foreground hover:text-destructive"
            >
              <Trash2 size={13} />
            </IconButton>
          </SimpleTooltip>
        </span>
        <Badge tone="muted" pill className="tabular-nums">
          {retentionRemainingLabel(folder.purgeAt)}
        </Badge>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`彻底删除「${folder.name}」？`}
        description={
          isLocal
            ? "将永久删除全部对话、云端文件与这张桌的设定，不可恢复。电脑上的文件夹不会被删除。"
            : "将永久删除全部对话、云端文件与这张桌的设定，不可恢复。"
        }
        confirmLabel="彻底删除"
        tone="danger"
        busy={purgeMutation.isPending}
        onConfirm={() => {
          purgeMutation.mutate(
            { id: folder.id },
            { onSuccess: () => setConfirmOpen(false) },
          );
        }}
      />
    </SurfaceRow>
  );
}
