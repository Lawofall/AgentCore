import { DirTypeIcon, FileTypeIcon } from "@/components/files/FileTypeIcon";
import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { AlertCircle, Loader2, MessageSquare, Users, X } from "lucide-react";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "./composerAttachments";

export function AttachmentChips({
  attachments,
  agentMentions = [],
  onRemove,
  onRemoveAgent,
}: {
  attachments: PendingAttachment[];
  agentMentions?: PendingAgentMention[];
  onRemove: (id: string) => void;
  onRemoveAgent?: (id: string) => void;
}) {
  if (attachments.length === 0 && agentMentions.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5 px-3 pt-3">
      {agentMentions.map((a) => (
        <span
          key={a.id}
          className="inline-flex max-w-[220px] items-center gap-1.5 rounded-lg bg-accent px-2 py-1 text-xs text-accent-foreground"
        >
          <Users size={12} className="shrink-0" />
          <span className="shrink-0 text-muted-foreground">点名</span>
          <SimpleTooltip label={a.role}>
            <span className="truncate">{a.role}</span>
          </SimpleTooltip>
          {onRemoveAgent && (
            <IconButton
              onClick={() => onRemoveAgent(a.id)}
              aria-label="移除角色点名"
              className="size-5 shrink-0"
            >
              <X size={12} />
            </IconButton>
          )}
        </span>
      ))}
      {attachments.map((a) => {
        const uploading = a.uploadState === "uploading";
        const failed = a.uploadState === "error";
        return (
          <span
            key={a.id}
            data-upload-state={a.uploadState}
            className={cn(
              "inline-flex max-w-[220px] items-center gap-1.5 rounded-lg px-2 py-1 text-xs",
              failed
                ? "bg-muted/40 text-muted-foreground"
                : "bg-accent text-accent-foreground",
            )}
          >
            {uploading ? (
              <Loader2
                size={12}
                className="shrink-0 animate-spin text-muted-foreground"
                aria-hidden
              />
            ) : failed ? (
              <AlertCircle size={12} className="shrink-0" aria-hidden />
            ) : a.kind === "dir" ? (
              <DirTypeIcon name={a.name} path={a.path} size={12} />
            ) : a.kind === "conversation" ? (
              <MessageSquare size={12} className="shrink-0" />
            ) : (
              <FileTypeIcon name={a.name} path={a.path} size={12} />
            )}
            {(uploading || failed) && (
              <span className="shrink-0 text-muted-foreground">
                {uploading ? "上传中" : "上传失败"}
              </span>
            )}
            <SimpleTooltip
              label={
                failed
                  ? (a.uploadError ?? "上传失败，发送时会重试")
                  : a.kind === "conversation"
                    ? "引用对话"
                    : a.path
              }
            >
              <span className="truncate">
                {a.name}
                {a.kind === "dir" ? "/" : ""}
              </span>
            </SimpleTooltip>
            {a.truncated && !uploading && !failed && (
              <span className="shrink-0 text-muted-foreground">
                {a.kind === "dir"
                  ? "部分"
                  : a.kind === "conversation"
                    ? "近期"
                    : "已截断"}
              </span>
            )}
            <IconButton
              onClick={() => onRemove(a.id)}
              aria-label="移除附件"
              className="size-5 shrink-0"
            >
              <X size={12} />
            </IconButton>
          </span>
        );
      })}
    </div>
  );
}
