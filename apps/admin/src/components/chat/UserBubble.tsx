import { CollapsibleBody } from "@/components/conversation-replay/shared";
import { Badge } from "@/components/ui/Badge";
import type { components } from "@/types/api.generated";
import { Fragment } from "react";

type StoredAttachment = components["schemas"]["StoredAttachment"];
type AgentMention = components["schemas"]["AgentMention"];

const KIND_LABEL: Record<StoredAttachment["kind"], string> = {
  file: "文件",
  dir: "文件夹",
  conversation: "会话",
  document: "文档",
};

const TOKEN_RE = /\uFFFC([AM])(\d+)\uFFFC/g;
const TOKEN_TEST = /\uFFFC[AM]\d+\uFFFC/;

type InlineSpan =
  | { kind: "text"; text: string }
  | { kind: "attachment"; index: number }
  | { kind: "mention"; index: number };

function hasInlineMarkers(content: string | null | undefined): boolean {
  if (!content) return false;
  return TOKEN_TEST.test(content);
}

/** Local parse — admin must not import desktop renderer. */
function parseInlineBody(content: string): InlineSpan[] {
  if (!content) return [];
  const spans: InlineSpan[] = [];
  let pos = 0;
  TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null = TOKEN_RE.exec(content);
  while (m) {
    if (m.index > pos) {
      spans.push({ kind: "text", text: content.slice(pos, m.index) });
    }
    const index = Number(m[2]);
    spans.push(
      m[1] === "A"
        ? { kind: "attachment", index }
        : { kind: "mention", index },
    );
    pos = m.index + m[0].length;
    m = TOKEN_RE.exec(content);
  }
  if (pos < content.length) {
    spans.push({ kind: "text", text: content.slice(pos) });
  }
  return spans;
}

function formatSize(bytes: number | null | undefined): string | null {
  if (bytes == null || bytes < 0) return null;
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
}

function ReplayMentionChip({ m }: { m: AgentMention }) {
  return (
    <span
      aria-label="@Agent"
      className="inline-flex items-center rounded-lg border border-border bg-card px-2 py-0.5 text-xs font-medium text-foreground"
    >
      @{m.role || m.agent_id}
    </span>
  );
}

function ReplayAttachmentChip({ a }: { a: StoredAttachment }) {
  return (
    <span
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
  );
}

/**
 * User-turn chips: attachment metadata + @Agent. No download, no file bytes.
 * Marked bodies render pills inline; unmarked keep the tray above the text.
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
  const marked = hasInlineMarkers(content);

  return (
    <div className="space-y-2">
      {!marked && (mentions.length > 0 || files.length > 0) && (
        <div
          data-testid="replay-chip-tray"
          className="flex flex-wrap justify-end gap-1.5"
        >
          {mentions.map((m) => (
            <ReplayMentionChip key={m.agent_id} m={m} />
          ))}
          {files.map((a, i) => (
            <ReplayAttachmentChip key={`${a.path}-${i}`} a={a} />
          ))}
        </div>
      )}
      {content ? (
        marked ? (
          <ReplayInlineBody
            content={content}
            files={files}
            mentions={mentions}
          />
        ) : (
          <CollapsibleBody content={content} fadeFrom="from-muted" />
        )
      ) : (
        <div className="text-muted-foreground text-sm italic">（无正文）</div>
      )}
    </div>
  );
}

function ReplayInlineBody({
  content,
  files,
  mentions,
}: {
  content: string;
  files: StoredAttachment[];
  mentions: AgentMention[];
}) {
  return (
    <div
      data-testid="user-inline-body"
      className="whitespace-pre-wrap break-words"
    >
      {parseInlineBody(content).map((span, i) => {
        if (span.kind === "text") {
          return (
            <Fragment key={i}>{span.text.replaceAll("\uFFFC", "")}</Fragment>
          );
        }
        if (span.kind === "attachment") {
          const a = files[span.index];
          if (!a) return null;
          return (
            <span key={i} className="mx-0.5 inline-flex align-middle">
              <ReplayAttachmentChip a={a} />
            </span>
          );
        }
        const m = mentions[span.index];
        if (!m) return null;
        return (
          <span key={i} className="mx-0.5 inline-flex align-middle">
            <ReplayMentionChip m={m} />
          </span>
        );
      })}
    </div>
  );
}
