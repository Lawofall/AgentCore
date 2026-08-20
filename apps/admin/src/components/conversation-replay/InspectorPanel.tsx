import {
  CollapsibleBody,
  EmptyPanel,
  STATUS_TONE,
  SpanRow,
} from "@/components/conversation-replay/shared";
import { LlmProcessRow, ToolLine } from "@/components/conversation-replay/ToolLine";
import { Badge } from "@/components/ui/Badge";
import {
  harvestKindLabel,
  isExecutionHarvestMessage,
} from "@/lib/executionHarvest";
import { cn, fmtMs } from "@/lib/utils";
import type {
  ReplayMessage,
  ReplayRun,
  ReplaySpan,
} from "@/services/adminObservability";
import { ArrowLeft, X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

/** Prefer run body; fall back to debrief.summary as plain message text. */
function runMessageBody(run: ReplayRun): string | null {
  if (run.content?.trim()) return run.content;
  const debrief = run.debrief;
  if (debrief && typeof debrief === "object") {
    const summary = (debrief as Record<string, unknown>).summary;
    if (typeof summary === "string" && summary.trim()) return summary;
  }
  return null;
}

/**
 * Right dock: harvest attribution, span details, worker full text.
 * Main column is user-perspective — this is where ops signals landed.
 */
export function InspectorPanel({
  message,
  selectedRunId,
  onSelectRun,
  onClearRun,
  onClose,
  cnyLabel,
  harvest,
  className,
}: {
  message: ReplayMessage;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  onClearRun: () => void;
  onClose: () => void;
  /** Pre-formatted turn cost for ops strip, e.g. "¥0.12". */
  cnyLabel?: string | null;
  /** Preceding harvest, when the selected row is the assistant that followed it. */
  harvest?: ReplayMessage | null;
  /** Height and width come from the page's layout row, not from the viewport. */
  className?: string;
}) {
  const runs = message.runs;
  const spans = message.spans;
  const selectedRun = useMemo(
    () => runs.find((r) => r.run_id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );
  const selfHarvest = isExecutionHarvestMessage(message);
  const shownHarvest = selfHarvest ? message : (harvest ?? null);

  return (
    <aside
      className={cn(
        "flex flex-col gap-0 overflow-hidden rounded-xl border border-border bg-card",
        className,
      )}
    >
      <div className="flex shrink-0 items-center justify-between gap-2 border-border border-b px-3 py-2">
        <span className="text-xs font-medium text-foreground">
          {selfHarvest ? "系统收口" : "队员"}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1 text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="关闭"
        >
          <X size={14} />
        </button>
      </div>
      {!selfHarvest && <OpsStrip message={message} cnyLabel={cnyLabel} />}
      <div className="min-h-0 flex-1 overflow-y-auto p-3 space-y-4">
        {shownHarvest && (
          <HarvestBlock
            message={shownHarvest}
            asTrigger={!selfHarvest}
          />
        )}
        {!selfHarvest && spans.length > 0 && (
          <TurnSpanList message={message} />
        )}
        {!selfHarvest && (
          <WorkerPanel
            run={selectedRun}
            runs={runs}
            spans={spans}
            selectedRunId={selectedRunId}
            onSelectRun={onSelectRun}
            onClearRun={onClearRun}
          />
        )}
      </div>
    </aside>
  );
}

function HarvestBlock({
  message,
  asTrigger,
}: {
  message: ReplayMessage;
  asTrigger: boolean;
}) {
  const kindLabel = harvestKindLabel(message.harvest_kind, message.content);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs font-medium text-foreground">
          {asTrigger ? "本回合由系统收口触发" : "系统收口"}
        </span>
        {kindLabel && (
          <Badge
            tone={
              kindLabel === "已取消"
                ? "warning"
                : kindLabel === "有失败"
                  ? "destructive"
                  : "success"
            }
          >
            {kindLabel}
          </Badge>
        )}
      </div>
      {message.content ? (
        <CollapsibleBody content={message.content} />
      ) : (
        <p className="text-muted-foreground text-xs italic">（无正文）</p>
      )}
    </div>
  );
}

function TurnSpanList({ message }: { message: ReplayMessage }) {
  const labels = useMemo(() => {
    const map = new Map<string, string>();
    for (const r of message.runs) {
      map.set(r.run_id, r.role || r.agent_id);
    }
    return map;
  }, [message.runs]);
  const multi = message.runs.length > 0;

  return (
    <div>
      <div className="mb-1.5 text-muted-foreground text-xs font-medium">
        过程明细 · {message.spans.length}
      </div>
      <div className="space-y-1.5">
        {message.spans.map((span, i) =>
          span.kind === "tool" ? (
            <ToolLine
              key={`tool-${i}`}
              span={span}
              runLabel={
                multi && span.run_id
                  ? (labels.get(span.run_id) ?? null)
                  : null
              }
            />
          ) : (
            <LlmProcessRow key={`llm-${i}`} span={span} />
          ),
        )}
      </div>
    </div>
  );
}

function OpsStrip({
  message,
  cnyLabel,
}: {
  message: ReplayMessage;
  cnyLabel?: string | null;
}) {
  const m = message.metrics;
  if (!m && !cnyLabel) return null;
  const isError = m?.status === "error";
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-border border-b px-3 py-2 text-xs text-muted-foreground">
      {m && (
        <Badge tone={isError ? "destructive" : "success"}>
          {m.finish_reason ?? m.status}
        </Badge>
      )}
      {m && <span className="tabular-nums">{m.rounds} 轮</span>}
      {m && <span className="tabular-nums">{fmtMs(m.duration_ms)}</span>}
      {cnyLabel && <span className="tabular-nums">{cnyLabel}</span>}
      {m?.delegated && (
        <span className="tabular-nums">委派 {m.workers}</span>
      )}
    </div>
  );
}

function WorkerPanel({
  run,
  runs,
  spans,
  selectedRunId,
  onSelectRun,
  onClearRun,
}: {
  run: ReplayRun | null;
  runs: ReplayRun[];
  spans: ReplaySpan[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  onClearRun: () => void;
}) {
  if (runs.length === 0) {
    return (
      <p className="text-muted-foreground text-xs">
        本回合无多 Agent 委派。工具与模型调用见上方过程明细。
      </p>
    );
  }

  if (!run) {
    return (
      <div>
        <p className="mb-2 text-muted-foreground text-xs">
          点选队员查看详情（主栏协作图亦可）
        </p>
        <RunTree
          runs={runs}
          selectedRunId={selectedRunId}
          onSelectRun={onSelectRun}
        />
      </div>
    );
  }

  const body = runMessageBody(run);
  const runSpans = spans.filter((s) => s.run_id === run.run_id);
  const tools = runSpans.filter((s) => s.kind === "tool");

  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={onClearRun}
        className="inline-flex items-center gap-1 text-muted-foreground text-xs outline-none hover:text-foreground focus-visible:underline"
      >
        <ArrowLeft size={12} />
        返回列表
      </button>

      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={STATUS_TONE[run.status] ?? "neutral"}>{run.status}</Badge>
        <span className="text-sm font-medium text-foreground">
          {run.role || run.agent_id}
        </span>
        {run.kind !== "agent" && (
          <span className="text-muted-foreground text-xs">{run.kind}</span>
        )}
      </div>

      {run.task && (
        <div>
          <div className="mb-0.5 text-muted-foreground text-xs font-medium">
            任务
          </div>
          <p className="max-h-32 max-w-full overflow-auto text-sm text-foreground whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
            {run.task}
          </p>
        </div>
      )}

      {run.output_summary && !body && (
        <p className="text-sm text-muted-foreground">{run.output_summary}</p>
      )}

      {body && (
        <div>
          <div className="mb-1 text-muted-foreground text-xs font-medium">
            产出
          </div>
          <div className="text-sm">
            <CollapsibleBody content={body} />
          </div>
        </div>
      )}

      {run.error && (
        <div className="rounded-lg bg-destructive/10 px-2.5 py-2 text-destructive text-xs">
          {run.error}
        </div>
      )}

      {tools.length > 0 && (
        <div>
          <div className="mb-1.5 text-muted-foreground text-xs font-medium">
            工具 · {tools.length}
          </div>
          <ol className="flex flex-col gap-1.5 border-border border-l pl-2">
            {tools.map((s, i) => (
              <SpanRow key={i} span={s} />
            ))}
          </ol>
        </div>
      )}

      {!body && !run.task && !run.error && tools.length === 0 && (
        <p className="text-muted-foreground text-xs italic">暂无队员明细</p>
      )}
    </div>
  );
}

function RunTree({
  runs,
  selectedRunId,
  onSelectRun,
}: {
  runs: ReplayRun[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
}) {
  const byParent = useMemo(() => {
    const map = new Map<string | null, ReplayRun[]>();
    for (const r of runs) {
      const key = r.parent_run_id ?? null;
      const list = map.get(key) ?? [];
      list.push(r);
      map.set(key, list);
    }
    return map;
  }, [runs]);

  const roots = byParent.get(null) ?? [];
  const known = new Set(runs.map((r) => r.run_id));
  const orphans = runs.filter(
    (r) => r.parent_run_id != null && !known.has(r.parent_run_id),
  );
  const top = roots.length > 0 ? roots : orphans.length > 0 ? orphans : runs;
  const nodeRefs = useRef<Map<string, HTMLElement>>(new Map());

  useEffect(() => {
    if (!selectedRunId) return;
    nodeRefs.current.get(selectedRunId)?.scrollIntoView?.({
      behavior: "smooth",
      block: "nearest",
    });
  }, [selectedRunId]);

  const renderNode = (run: ReplayRun, depth: number) => {
    const children = byParent.get(run.run_id) ?? [];
    const active = selectedRunId === run.run_id;
    return (
      <li key={run.run_id} className="mb-1">
        <button
          type="button"
          ref={(node) => {
            if (node) nodeRefs.current.set(run.run_id, node);
            else nodeRefs.current.delete(run.run_id);
          }}
          onClick={() => onSelectRun(run.run_id)}
          className={cn(
            "w-full rounded-lg border px-2 py-1.5 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
            active
              ? "border-primary/50 bg-primary/10 ring-1 ring-primary/30"
              : "border-border/60 bg-muted/30 hover:bg-muted/50",
          )}
          style={{ marginLeft: depth * 12 }}
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={STATUS_TONE[run.status] ?? "neutral"}>
              {run.status}
            </Badge>
            <span className="text-foreground text-xs font-medium">
              {run.role || run.agent_id}
            </span>
            {run.kind !== "agent" && (
              <span className="text-muted-foreground text-xs">{run.kind}</span>
            )}
          </div>
          {run.task && (
            <p className="mt-0.5 text-muted-foreground text-xs line-clamp-2 break-words">
              {run.task}
            </p>
          )}
        </button>
        {children.length > 0 && (
          <ul className="mt-1">{children.map((c) => renderNode(c, depth + 1))}</ul>
        )}
      </li>
    );
  };

  return <ul>{top.map((r) => renderNode(r, 0))}</ul>;
}

/** Narrow-screen empty — kept for callers that need a placeholder. */
export function InspectorEmpty() {
  return <EmptyPanel text="点选协作图中的队员查看详情" />;
}
