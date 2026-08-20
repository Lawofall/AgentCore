import { Markdown } from "@/components/Markdown";
import { Badge } from "@/components/ui/Badge";
import { CLAMPED_PRE_CLASS } from "@/lib/clampPreview";
import { cn } from "@/lib/utils";
import type { ReplaySpan } from "@/services/adminObservability";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

export const ROLE_LABEL: Record<string, string> = {
  user: "用户",
  assistant: "助手",
  system: "系统",
};

export type MobileTab = "timeline" | "team";
export const STATUS_TONE: Record<
  string,
  "neutral" | "primary" | "success" | "warning" | "destructive"
> = {
  pending: "neutral",
  running: "primary",
  completed: "success",
  failed: "destructive",
  cancelled: "warning",
  skipped: "neutral",
};

const COLLAPSE_CHARS = 480;
const COLLAPSE_LINES = 10;

export function formatProcessSummary(
  llmCount: number,
  toolCount: number,
): string {
  const parts: string[] = [];
  if (llmCount > 0) parts.push(`${llmCount} 次模型调用`);
  if (toolCount > 0) parts.push(`${toolCount} 次工具`);
  return parts.join(" · ") || "过程";
}

export function credentialSourceLabel(
  source: "user" | "platform" | null | undefined,
): string | null {
  if (source === "user") return "BYOK";
  if (source === "platform") return "平台";
  return null;
}

export function normText(s: string): string {
  return s.trim().replace(/\s+/g, " ");
}

export function EmptyPanel({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-border bg-card py-10 text-center text-muted-foreground text-sm">
      {text}
    </div>
  );
}

export function CollapsibleBody({ content }: { content: string }) {
  const long =
    content.length > COLLAPSE_CHARS ||
    content.split("\n").length > COLLAPSE_LINES;
  const [expanded, setExpanded] = useState(!long);

  if (!long) {
    return <Markdown content={content} />;
  }

  return (
    <div className="min-w-0 max-w-full">
      <div
        className={cn(
          "relative min-w-0",
          !expanded && "max-h-[11rem] overflow-hidden",
        )}
      >
        <Markdown content={content} />
        {!expanded && (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-t from-card to-transparent"
          />
        )}
      </div>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setExpanded((v) => !v);
        }}
        className="mt-2 text-primary text-xs font-medium outline-none hover:underline focus-visible:underline"
      >
        {expanded ? "收起" : "展开全文"}
      </button>
    </div>
  );
}

export function SpanRow({ span }: { span: ReplaySpan }) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(span.args_preview || span.result_preview);

  if (span.kind === "tool") {
    return (
      <li className="text-xs">
        <button
          type="button"
          onClick={() => hasDetail && setOpen((v) => !v)}
          disabled={!hasDetail}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-0.5 py-0.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring",
            hasDetail
              ? "hover:bg-muted/50 cursor-pointer"
              : "cursor-default",
          )}
        >
          {hasDetail ? (
            open ? (
              <ChevronDown
                size={12}
                className="shrink-0 text-muted-foreground"
              />
            ) : (
              <ChevronRight
                size={12}
                className="shrink-0 text-muted-foreground"
              />
            )
          ) : (
            <span className="inline-block w-3 shrink-0" />
          )}
          <Badge tone={span.success === false ? "destructive" : "neutral"}>
            工具
          </Badge>
          <span className="min-w-0 flex-1 truncate font-medium text-foreground">
            {span.name ?? "—"}
          </span>
          <span
            className={
              span.success === false ? "text-destructive" : "text-success"
            }
          >
            {span.success === false ? "失败" : "成功"}
          </span>
        </button>
        {open && hasDetail && (
          <div className="mt-1 space-y-1 pl-4">
            {span.args_preview && (
              <pre
                className={cn(
                  CLAMPED_PRE_CLASS,
                  "rounded bg-muted px-2 py-1 font-mono text-[11px] text-muted-foreground",
                )}
              >
                {span.args_preview}
              </pre>
            )}
            {span.result_preview && (
              <pre
                className={cn(
                  CLAMPED_PRE_CLASS,
                  "rounded bg-muted/60 px-2 py-1 font-mono text-[11px] text-muted-foreground",
                )}
              >
                → {span.result_preview}
              </pre>
            )}
          </div>
        )}
      </li>
    );
  }
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      <Badge tone="primary">LLM</Badge>
      {span.round_idx != null && (
        <span className="text-muted-foreground tabular-nums">
          第 {span.round_idx + 1} 轮
        </span>
      )}
      {span.finish_reason && (
        <span className="text-muted-foreground">{span.finish_reason}</span>
      )}
      <span className="text-muted-foreground tabular-nums">
        ↑{span.input_tokens ?? 0} ↓{span.output_tokens ?? 0}
      </span>
    </li>
  );
}
