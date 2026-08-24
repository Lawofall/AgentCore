import { Markdown } from "@/components/Markdown";
import { Badge } from "@/components/ui/Badge";
import {
  type NormalizedInteraction,
  resolvedAskForStep,
} from "@/components/chat/chatTurn";
import { CLAMPED_PRE_CLASS, clampPreview } from "@/lib/clampPreview";
import { cn } from "@/lib/utils";
import type { ProcessStep } from "@agentcore/protocol-conformance";
import { Check, ChevronDown, ChevronRight, Wrench, X } from "lucide-react";
import { Fragment, type ReactNode, useState } from "react";

const TOOL_TONE: Record<string, "primary" | "success" | "destructive" | "neutral"> =
  {
    running: "primary",
    success: "success",
    error: "destructive",
  };

type ToolStep = Extract<ProcessStep, { kind: "tool" }>;

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

/** First non-empty line of thought, stripped of common markdown — for collapsed previews. */
export function reasoningPlainPreview(text: string): string {
  const line =
    text
      .trim()
      .split(/\r?\n/)
      .find((l) => l.trim()) ?? "";
  return line
    .replace(/^#{1,6}\s+/, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .trim();
}

type LaneRow =
  | { key: string; folded: true; kind: "reasoning"; text: string }
  | { key: string; folded: true; kind: "tool"; step: ToolStep }
  | { key: string; folded: true; kind: "tool-group"; tools: ToolStep[] }
  | { key: string; folded: false; kind: "content"; text: string }
  | { key: string; folded: false; kind: "slot"; step: ProcessStep };

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

  let toolRun: ToolStep[] = [];
  const flushTools = () => {
    if (toolRun.length === 0) return;
    if (toolRun.length === 1) {
      const step = toolRun[0];
      rows.push({
        key: `tool-${step.id ?? toolRun.length}`,
        folded: true,
        kind: "tool",
        step,
      });
    } else {
      rows.push({
        key: `tgrp-${toolRun[0].id ?? toolRun.length}`,
        folded: true,
        kind: "tool-group",
        tools: [...toolRun],
      });
    }
    toolRun = [];
  };

  steps.forEach((step, i) => {
    if ((step as { kind: string }).kind === "ask") return;
    if (step.kind === "tool") {
      toolRun.push(step);
      return;
    }
    flushTools();
    const key = `${step.kind}-${i}`;
    if (step.kind === "reasoning") {
      rows.push({ key, folded: true, kind: "reasoning", text: step.text });
      return;
    }
    if (step.kind === "content") {
      if (hideContentSteps) return;
      rows.push({ key, folded: false, kind: "content", text: step.text });
      return;
    }
    rows.push({ key, folded: false, kind: "slot", step });
  });
  flushTools();
  return rows;
}

function countTools(rows: LaneRow[]): number {
  let n = 0;
  for (const row of rows) {
    if (row.kind === "tool") n += 1;
    else if (row.kind === "tool-group") n += row.tools.length;
  }
  return n;
}

/**
 * CEO process lane: settled fold matches the desktop bubble.
 * Reasoning/tools collapse to a one-line summary (except a single pure thought);
 * collapsed state still shows a one-line thought preview. Content steps that
 * duplicate the deliverable are omitted by the caller.
 */
export function ProcessLane({
  steps,
  fallbackReasoning = "",
  hideContentSteps = false,
  collapse = true,
  interactions = [],
  fallbackContent = null,
  team = null,
  className,
}: {
  steps: ProcessStep[];
  fallbackReasoning?: string;
  hideContentSteps?: boolean;
  /** CEO bubble folds thought/tools; worker dock keeps the timeline open. */
  collapse?: boolean;
  interactions?: NormalizedInteraction[];
  /** CEO body, injected before the first `team` marker (desktop fallback-before-team). */
  fallbackContent?: ReactNode;
  team?: ReactNode;
  className?: string;
}) {
  const rows = buildRows(steps, fallbackReasoning, hideContentSteps);
  const reasoningCount = rows.filter((r) => r.kind === "reasoning").length;
  const toolCount = countTools(rows);
  const shouldCollapse =
    collapse &&
    (reasoningCount > 0 || toolCount > 0) &&
    !(reasoningCount === 1 && toolCount === 0);
  const [expanded, setExpanded] = useState(false);
  const reasoningDefaultExpanded = !collapse;
  const firstReasoningPreview = rows
    .filter((r) => r.kind === "reasoning")
    .map((r) => reasoningPlainPreview(r.text))
    .find((p) => p.length > 0);
  const hasTeamSlot = rows.some(
    (r) => r.kind === "slot" && r.step.kind === "team",
  );
  let consumedFallback = false;
  let consumedTeam = false;

  if (rows.length === 0 && !fallbackContent && !team) return null;

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

  const outerPreview =
    shouldCollapse && !expanded && firstReasoningPreview ? (
      <p
        className="line-clamp-1 min-w-0 break-words text-sm text-muted-foreground"
        title={firstReasoningPreview}
      >
        {firstReasoningPreview}
      </p>
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
        const isFirstTeam =
          row.kind === "slot" &&
          row.step.kind === "team" &&
          !rows
            .slice(0, i)
            .some((r) => r.kind === "slot" && r.step.kind === "team");
        if (isFirstTeam) {
          if (fallbackContent) consumedFallback = true;
          if (team) consumedTeam = true;
        }
        const view = (
          <LaneRowView
            row={row}
            interactions={interactions}
            fallbackContent={isFirstTeam ? fallbackContent : null}
            team={isFirstTeam ? team : null}
            reasoningDefaultExpanded={reasoningDefaultExpanded}
          />
        );
        if (shouldCollapse && row.folded) {
          const isFirstFolded = rows.slice(0, i).every((r) => !r.folded);
          if (!expanded) {
            if (!isFirstFolded) return null;
            return (
              <Fragment key={`sum-${row.key}`}>
                <div className="min-w-0 space-y-1">
                  {summaryButton}
                  {outerPreview}
                </div>
              </Fragment>
            );
          }
          if (isFirstFolded) {
            return (
              <Fragment key={`open-${row.key}`}>
                {summaryButton}
                {view}
              </Fragment>
            );
          }
        }
        return <Fragment key={row.key}>{view}</Fragment>;
      })}
      {!hasTeamSlot && !consumedFallback && fallbackContent}
      {!hasTeamSlot && !consumedTeam && team}
    </div>
  );
}

function LaneRowView({
  row,
  interactions,
  fallbackContent,
  team,
  reasoningDefaultExpanded,
}: {
  row: LaneRow;
  interactions?: NormalizedInteraction[];
  fallbackContent?: ReactNode;
  team?: ReactNode;
  reasoningDefaultExpanded: boolean;
}) {
  if (row.kind === "reasoning") {
    return (
      <InlineReasoning
        text={row.text}
        defaultExpanded={reasoningDefaultExpanded}
      />
    );
  }
  if (row.kind === "tool") {
    return <ToolCard step={row.step} />;
  }
  if (row.kind === "tool-group") {
    return <ToolGroup tools={row.tools} />;
  }
  if (row.kind === "content") {
    return (
      <div className="max-w-full min-w-0 text-foreground">
        <Markdown content={row.text} />
      </div>
    );
  }
  return (
    <SlotRow
      step={row.step}
      interactions={interactions ?? []}
      fallbackContent={fallbackContent}
      team={team}
    />
  );
}

function stepCount(step: ProcessStep, ...keys: string[]): number {
  const rec = step as unknown as Record<string, unknown>;
  for (const key of keys) {
    const value = rec[key];
    if (typeof value === "number" && Number.isFinite(value) && value > 0) {
      return value;
    }
    if (typeof value === "string" && Number(value) > 0) return Number(value);
  }
  return 0;
}

function SlotRow({
  step,
  interactions,
  fallbackContent,
  team,
}: {
  step: ProcessStep;
  interactions: NormalizedInteraction[];
  fallbackContent?: ReactNode;
  team?: ReactNode;
}) {
  if (step.kind === "team") {
    if (!fallbackContent && !team) return null;
    return (
      <>
        {fallbackContent}
        {team}
      </>
    );
  }
  if (step.kind === "checkpoint" || (step.kind as string) === "ask") {
    const ask = resolvedAskForStep(step, interactions);
    if (!ask) return null;
    return <AskReadCard ask={ask} />;
  }
  if (step.kind === "rework") {
    return (
      <span className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground">
        已重写
      </span>
    );
  }
  if (step.kind === "graph_append") {
    const added = stepCount(step, "added_count", "addedCount");
    if (!added) return null;
    return <p className="text-muted-foreground text-xs">续图 · +{added}</p>;
  }
  return null;
}

function AskReadCard({ ask }: { ask: NormalizedInteraction }) {
  return (
    <aside
      aria-label="提问"
      className="min-w-0 max-w-full rounded-lg border border-border bg-muted/30 px-3 py-2"
    >
      <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        <span>提问</span>
        {ask.status && (
          <Badge tone={ask.status === "resolved" ? "success" : "neutral"}>
            {ask.status}
          </Badge>
        )}
      </div>
      <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-foreground text-sm">
        {ask.question}
      </p>
    </aside>
  );
}

function InlineReasoning({
  text,
  defaultExpanded,
}: {
  text: string;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const preview = reasoningPlainPreview(text);
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
      {expanded ? (
        <div className="mt-1.5 max-h-64 max-w-full overflow-auto text-foreground">
          <Markdown content={text} />
        </div>
      ) : preview ? (
        <p
          className="mt-1 line-clamp-1 min-w-0 break-words text-sm text-muted-foreground"
          title={preview}
        >
          {preview}
        </p>
      ) : null}
    </div>
  );
}

function ToolGroup({ tools }: { tools: ToolStep[] }) {
  const [open, setOpen] = useState(false);
  const label = `使用 ${tools.length} 个工具`;
  return (
    <div className="min-w-0">
      <button
        type="button"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring rounded-lg"
      >
        <Wrench size={14} className="shrink-0" aria-hidden />
        <span>{label}</span>
        {open ? (
          <ChevronDown size={14} className="shrink-0" aria-hidden />
        ) : (
          <ChevronRight size={14} className="shrink-0" aria-hidden />
        )}
      </button>
      {open && (
        <div className="mt-1.5 space-y-2 pl-3">
          {tools.map((step, i) => (
            <ToolCard key={step.id ?? i} step={step} />
          ))}
        </div>
      )}
    </div>
  );
}

function ToolCard({
  step,
}: {
  step: ToolStep;
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
  const isRedirect = status === "redirect";
  const hasBody =
    args != null ||
    result != null ||
    display != null ||
    (isRedirect && Boolean(failMessage));

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
        <Wrench size={14} className="shrink-0" aria-hidden />
        <span className="min-w-0 flex-1 truncate" title={name}>
          {name}
        </span>
        {status === "success" ? (
          <Check size={14} className="shrink-0 text-success" aria-label="success" />
        ) : status === "redirect" ? (
          <Badge className="shrink-0" tone="neutral">
            改道
          </Badge>
        ) : status === "error" ? (
          <X size={14} className="shrink-0 text-destructive" aria-label="error" />
        ) : status ? (
          <Badge className="shrink-0" tone={TOOL_TONE[status] ?? "neutral"}>
            {status}
          </Badge>
        ) : null}
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
            <p
              className={
                isRedirect
                  ? "text-muted-foreground text-xs"
                  : "text-destructive text-xs"
              }
            >
              {failMessage}
            </p>
          )}
          {args != null && (
            <ClampedDump label="工具参数" value={args} tone="bg-muted" />
          )}
          {result != null && !isRedirect && (
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
