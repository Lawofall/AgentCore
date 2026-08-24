import { CollapsibleBody } from "@/components/conversation-replay/shared";
import { Badge } from "@/components/ui/Badge";
import type { components } from "@/types/api.generated";

type StoredAttachment = components["schemas"]["StoredAttachment"];
type AgentMention = components["schemas"]["AgentMention"];

const KIND_LABEL: Record<StoredAttachment["kind"], string> = {
  file: "文件",
  dir: "文件夹",
  conversation: "会话",
};

function formatSize(bytes: number | null | undefined): string | null {
  if (bytes == null || bytes < 0) return null;
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
}

/**
 * User-turn chips: attachment metadata + @Agent. No download, no file bytes.
 */
export function UserBubble({
  content,
  attachments,
  agentMentions,
}: {
  content?: string | null;
  attachments?: StoredAttachment[] | null;
  agentMentions?: AgentMention[] | null;
}) {
  const files = attachments ?? [];
  const mentions = agentMentions ?? [];

  return (
    <div className="space-y-2">
      {(mentions.length > 0 || files.length > 0) && (
        <div className="flex flex-wrap justify-end gap-1.5">
          {mentions.map((m) => (
            <span
              key={m.agent_id}
              aria-label="@Agent"
              className="inline-flex items-center rounded-lg border border-border bg-card px-2 py-0.5 text-xs font-medium text-foreground"
            >
              @{m.role || m.agent_id}
            </span>
          ))}
          {files.map((a, i) => (
            <span
              key={`${a.path}-${i}`}
              aria-label="附件"
              className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border bg-card px-2 py-0.5 text-xs text-muted-foreground"
            >
              <span>{KIND_LABEL[a.kind] ?? a.kind}</span>
              <span className="truncate font-medium text-foreground">
                {a.name || a.path}
              </span>
              {formatSize(a.size_bytes) && (
                <span className="tabular-nums">{formatSize(a.size_bytes)}</span>
              )}
              {a.truncated && <Badge tone="warning">截断</Badge>}
            </span>
          ))}
        </div>
      )}
      {content ? (
        <CollapsibleBody content={content} fadeFrom="from-muted" />
      ) : (
        <div className="text-muted-foreground text-sm italic">（无正文）</div>
      )}
    </div>
  );
}
