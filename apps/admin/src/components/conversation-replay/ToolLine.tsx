import { Badge } from "@/components/ui/Badge";
import { CLAMPED_PRE_CLASS } from "@/lib/clampPreview";
import { cn } from "@/lib/utils";
import type { ReplaySpan } from "@/services/adminObservability";
import { Check, ChevronDown, ChevronRight, Wrench, X } from "lucide-react";
import { useState } from "react";

/** Read-only tool row for admin replay process timeline (visual echo of desktop ToolLine). */
export function ToolLine({
  span,
  runLabel,
}: {
  span: ReplaySpan;
  /** Optional multi-agent run chip (role / agent). */
  runLabel?: string | null;
}) {
  const hasBody = Boolean(span.args_preview || span.result_preview);
  const [open, setOpen] = useState(false);
  const failed = span.success === false;
  const name = span.name?.trim() || "工具";

  return (
    <div className="min-w-0">
      <button
        type="button"
        disabled={!hasBody}
        onClick={(e) => {
          e.stopPropagation();
          if (hasBody) setOpen((v) => !v);
        }}
        className={cn(
          "flex w-full items-start gap-2 rounded-lg px-0 py-0.5 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring",
          hasBody
            ? "cursor-pointer text-muted-foreground hover:text-foreground"
            : "cursor-default text-muted-foreground",
        )}
      >
        <Wrench size={14} className="mt-0.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate">
          <span className="font-medium text-foreground">{name}</span>
          {runLabel && (
            <span className="ml-1.5 text-muted-foreground">· {runLabel}</span>
          )}
        </span>
        <span className="ml-1 inline-flex shrink-0 items-center gap-1">
          {failed ? (
            <X size={14} className="text-destructive" />
          ) : (
            <Check size={14} className="text-success" />
          )}
          {hasBody &&
            (open ? (
              <ChevronDown size={14} className="text-muted-foreground" />
            ) : (
              <ChevronRight size={14} className="text-muted-foreground" />
            ))}
        </span>
      </button>
      {open && hasBody && (
        <div className="mt-1 space-y-1.5 border-border border-l pl-3 ml-1.5">
          {span.args_preview && (
            <pre
              className={cn(
                CLAMPED_PRE_CLASS,
                "rounded-lg bg-muted px-2.5 py-1.5 font-mono text-xs text-muted-foreground",
              )}
            >
              {span.args_preview}
            </pre>
          )}
          {span.result_preview && (
            <pre
              className={cn(
                CLAMPED_PRE_CLASS,
                "rounded-lg bg-muted/60 px-2.5 py-1.5 font-mono text-xs text-muted-foreground",
              )}
            >
              → {span.result_preview}
            </pre>
          )}
          {failed && (
            <Badge tone="destructive" className="mt-0.5">
              失败
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}

/** Compact LLM round row — stand-in for「思考」when snapshot has no reasoning text. */
export function LlmProcessRow({ span }: { span: ReplaySpan }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm text-muted-foreground">
      <span className="font-medium text-foreground">模型调用</span>
      {span.round_idx != null && (
        <span className="tabular-nums">第 {span.round_idx + 1} 轮</span>
      )}
      {span.finish_reason && <span>{span.finish_reason}</span>}
      <span className="tabular-nums">
        ↑{span.input_tokens ?? 0} ↓{span.output_tokens ?? 0}
      </span>
    </div>
  );
}
