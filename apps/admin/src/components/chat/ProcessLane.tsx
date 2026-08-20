import { Badge } from "@/components/ui/Badge";
import { CLAMPED_PRE_CLASS, clampPreview } from "@/lib/clampPreview";
import { cn } from "@/lib/utils";
import type { ProcessStep } from "@agentcore/protocol-conformance";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";

const MARKER_LABEL: Record<string, string> = {
  team: "协作图",
  graph_append: "续图",
  checkpoint: "提问",
  ask: "提问",
  plan_review: "计划复核",
  team_preview: "开工卡",
  escalation: "升级",
  approval: "审批",
  stage_card: "推进卡",
  user_interjection: "插话",
  rework: "已重写",
};

const TOOL_TONE: Record<string, "primary" | "success" | "destructive" | "neutral"> =
  {
    running: "primary",
    success: "success",
    error: "destructive",
  };

function formatClamped(
  value: unknown,
): { text: string; truncated: boolean } | null {
  if (value == null) return null;
  let raw: string;
  if (typeof value === "string") {
    raw = value;
  } else {
    try {
      raw = JSON.stringify(value, null, 2);
    } catch {
      raw = String(value);
    }
  }
  if (!raw) return null;
  return clampPreview(raw);
}

function formatLaneSummary(reasoningCount: number, toolCount: number): string {
  const parts: string[] = [];
  if (reasoningCount > 0) parts.push(`思考 ${reasoningCount} 步`);
  if (toolCount > 0) parts.push(`使用 ${toolCount} 个工具`);
  return parts.join(" · ");
}

type LaneRow =
  | { key: string; folded: true; kind: "reasoning"; text: string }
  | {
      key: string;
      folded: true;
      kind: "tool";
      step: Extract<ProcessStep, { kind: "tool" }>;
    }
  | { key: string; folded: false; kind: "content"; text: string }
  | { key: string; folded: false; kind: "marker"; label: string };

function buildRows(
  steps: ProcessStep[],
  fallbackReasoning: string,
  hideContentSteps: boolean,
): LaneRow[] {
  const rows: LaneRow[] = [];
  const hasReasoningStep = steps.some((s) => s.kind === "reasoning");
  if (!hasReasoningStep && fallbackReasoning.trim()) {
    rows.push({
      key: "fallback-reason",
      folded: true,
      kind: "reasoning",
      text: fallbackReasoning,
    });
  }
  steps.forEach((step, i) => {
    const key = `${step.kind}-${i}`;
    if (step.kind === "reasoning") {
      rows.push({ key, folded: true, kind: "reasoning", text: step.text });
      return;
    }
    if (step.kind === "tool") {
      rows.push({ key, folded: true, kind: "tool", step });
      return;
    }
    if (step.kind === "content") {
      if (hideContentSteps) return;
      rows.push({ key, folded: false, kind: "content", text: step.text });
      return;
    }
    rows.push({
      key,
      folded: false,
      kind: "marker",
      label: MARKER_LABEL[step.kind] ?? step.kind,
    });
  });
  return rows;
}

/**
 * CEO process lane: settled fold matches the desktop bubble.
 * Reasoning/tools collapse to a one-line summary (except a single pure thought);
 * expanded rows still hide thought body and tool JSON until clicked. Content
 * steps that duplicate the deliverable are omitted by the caller.
 */
export function ProcessLane({
  steps,
  fallbackReasoning = "",
  hideContentSteps = false,
  className,
}: {
  steps: ProcessStep[];
  fallbackReasoning?: string;
  hideContentSteps?: boolean;
  className?: string;
}) {
  const rows = buildRows(steps, fallbackReasoning, hideContentSteps);
  const reasoningCount = rows.filter((r) => r.kind === "reasoning").length;
  const toolCount = rows.filter((r) => r.kind === "tool").length;
  const shouldCollapse =
    (reasoningCount > 0 || toolCount > 0) &&
    !(reasoningCount === 1 && toolCount === 0);
  const [expanded, setExpanded] = useState(false);

  if (rows.length === 0) return null;

  const summary = formatLaneSummary(reasoningCount, toolCount);
  const summaryButton = shouldCollapse ? (
    <button
      type="button"
      aria-expanded={expanded}
      onClick={(e) => {
        e.stopPropagation();
        setExpanded((v) => !v);
      }}
      className="inline-flex items-center gap-1 text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring rounded-lg"
    >
      {summary}
      {expanded ? (
        <ChevronDown size={14} className="shrink-0" aria-hidden />
      ) : (
        <ChevronRight size={14} className="shrink-0" aria-hidden />
      )}
    </button>
  ) : null;

  return (
    <div
      aria-label="过程"
      className={cn(
        "min-w-0 max-w-full space-y-2 text-sm text-muted-foreground",
        className,
      )}
    >
      {rows.map((row, i) => {
        if (shouldCollapse && row.folded) {
          const isFirstFolded = rows.slice(0, i).every((r) => !r.folded);
          if (!expanded) {
            if (!isFirstFolded) return null;
            return <Fragment key={`sum-${row.key}`}>{summaryButton}</Fragment>;
          }
          if (isFirstFolded) {
            return (
              <Fragment key={`open-${row.key}`}>
                {summaryButton}
                <LaneRowView row={row} />
              </Fragment>
            );
          }
        }
        return <LaneRowView key={row.key} row={row} />;
      })}
    </div>
  );
}

function LaneRowView({ row }: { row: LaneRow }) {
  if (row.kind === "reasoning") {
    return <InlineReasoning text={row.text} />;
  }
  if (row.kind === "tool") {
    return <ToolCard step={row.step} />;
  }
  if (row.kind === "content") {
    return (
      <p className="max-w-full whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-foreground">
        {row.text}
      </p>
    );
  }
  return <p>{row.label}</p>;
}

function InlineReasoning({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="min-w-0">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={(e) => {
          e.stopPropagation();
          setExpanded((v) => !v);
        }}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring rounded-lg"
      >
        思考
        {expanded ? (
          <ChevronDown size={14} className="shrink-0" aria-hidden />
        ) : (
          <ChevronRight size={14} className="shrink-0" aria-hidden />
        )}
      </button>
      {expanded && (
        <p className="mt-1.5 max-h-64 max-w-full overflow-auto whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-foreground">
          {text}
        </p>
      )}
    </div>
  );
}

function ToolCard({
  step,
}: {
  step: Extract<ProcessStep, { kind: "tool" }>;
}) {
  const [open, setOpen] = useState(false);
  const name =
    "tool_name" in step && typeof step.tool_name === "string"
      ? step.tool_name
      : "工具";
  const status = "status" in step && typeof step.status === "string" ? step.status : "";
  const args = formatClamped("arguments" in step ? step.arguments : undefined);
  const result = formatClamped("result" in step ? step.result : undefined);
  const display = formatClamped("display" in step ? step.display : undefined);
  const failure =
    "failure" in step && step.failure && typeof step.failure === "object"
      ? step.failure
      : null;
  const failMessage =
    failure && "message" in failure && typeof failure.message === "string"
      ? failure.message
      : null;
  const hasBody = args != null || result != null || display != null;

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
          "flex w-full items-center gap-2 rounded-lg px-0 py-0.5 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring",
          hasBody
            ? "cursor-pointer text-muted-foreground hover:text-foreground"
            : "cursor-default text-muted-foreground",
        )}
      >
        <span className="min-w-0 flex-1 truncate font-medium text-foreground" title={name}>
          {name}
        </span>
        {status && (
          <Badge className="shrink-0" tone={TOOL_TONE[status] ?? "neutral"}>
            {status}
          </Badge>
        )}
        {hasBody &&
          (open ? (
            <ChevronDown size={14} className="ml-auto shrink-0" aria-hidden />
          ) : (
            <ChevronRight size={14} className="ml-auto shrink-0" aria-hidden />
          ))}
      </button>
      {open && hasBody && (
        <div className="mt-1 space-y-1.5">
          {failMessage && (
            <p className="text-destructive text-xs">{failMessage}</p>
          )}
          {args != null && (
            <ClampedDump label="工具参数" value={args} tone="bg-muted" />
          )}
          {result != null && (
            <ClampedDump label="工具结果" value={result} tone="bg-muted/60" />
          )}
          {display != null && (
            <ClampedDump label="工具展示" value={display} tone="bg-muted/40" />
          )}
        </div>
      )}
    </div>
  );
}

function ClampedDump({
  label,
  value,
  tone,
}: {
  label: string;
  value: { text: string; truncated: boolean };
  tone: string;
}) {
  return (
    <div className="min-w-0 max-w-full">
      <pre
        aria-label={label}
        className={cn(
          CLAMPED_PRE_CLASS,
          "rounded-lg px-2.5 py-1.5 font-mono text-xs text-muted-foreground",
          tone,
        )}
      >
        {value.text}
      </pre>
      {value.truncated && (
        <p className="mt-0.5 text-muted-foreground text-xs">已截断</p>
      )}
    </div>
  );
}
