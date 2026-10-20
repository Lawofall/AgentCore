import { DirTypeIcon, FileTypeIcon } from "@/components/files/FileTypeIcon";
import { Button } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import type { MessageAttachmentMeta } from "@/stores/conversation";
import { useSidePanelStore } from "@/stores/sidePanel";
import { Bookmark, MessageSquare } from "lucide-react";

const chipClass =
  "inline-flex max-w-[220px] items-center gap-1.5 rounded-lg bg-accent px-2 py-1 text-xs text-accent-foreground";

function previewPath(att: MessageAttachmentMeta): string | undefined {
  const folder = att.archiveDir?.trim();
  if (folder) return folder;
  return att.workspacePath;
}

function canOpenPreview(att: MessageAttachmentMeta): boolean {
  return (
    Boolean(previewPath(att)) &&
    att.kind !== "dir" &&
    att.kind !== "conversation" &&
    att.kind !== "document"
  );
}

export function AttachmentChip({
  att,
  interactive = false,
}: {
  att: MessageAttachmentMeta;
  /** History / interjection only — composer and edit draft stay labels. */
  interactive?: boolean;
}) {
  const showFile = useSidePanelStore((s) => s.showFile);
  const openable = interactive && canOpenPreview(att);
  const openPath = previewPath(att);
  const asFolder = Boolean(att.archiveDir) || att.kind === "dir";
  const icon = asFolder ? (
    <DirTypeIcon name={att.name} path={att.archiveDir || att.path} size={12} />
  ) : att.kind === "conversation" ? (
    <MessageSquare size={12} className="shrink-0" />
  ) : att.kind === "document" ? (
    <Bookmark size={12} className="shrink-0" />
  ) : (
    <FileTypeIcon name={att.name} path={att.path} size={12} />
  );
  const tooltip =
    att.kind === "conversation"
      ? "引用对话"
      : att.kind === "document"
        ? "本句点名提示词"
        : att.path;
  const face = (
    <>
      {icon}
      <span className="truncate">
        {att.name}
        {asFolder ? "/" : ""}
      </span>
      {att.truncated && (
        <span className="shrink-0 text-muted-foreground">
          {att.kind === "dir"
            ? "部分"
            : att.kind === "conversation"
              ? "近期"
              : "已截断"}
        </span>
      )}
    </>
  );

  if (!openable) {
    return (
      <SimpleTooltip label={tooltip}>
        <span className={chipClass}>{face}</span>
      </SimpleTooltip>
    );
  }

  return (
    <SimpleTooltip label={tooltip}>
      <Button
        variant="ghost"
        aria-label={`打开 ${att.name}`}
        onClick={() =>
          showFile(
            openPath as string,
            asFolder ? att.name.replace(/\.[^.]+$/, "") || att.name : att.name,
          )
        }
        className={`${chipClass} h-auto font-normal transition-colors hover:bg-accent/70`}
      >
        {face}
      </Button>
    </SimpleTooltip>
  );
}
