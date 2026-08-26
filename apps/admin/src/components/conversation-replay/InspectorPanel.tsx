import { CopyableId } from "@/components/CopyableId";
import { ProcessLane } from "@/components/chat/ProcessLane";
import {
  chatTurnFromReplay,
  resolveChatTurn,
} from "@/components/chat/chatTurn";
import {
  CollapsibleBody,
  EmptyPanel,
  STATUS_TONE,
} from "@/components/conversation-replay/shared";
import { LlmProcessRow, ToolLine } from "@/components/conversation-replay/ToolLine";
import { TurnOpsBar } from "@/components/conversation-replay/TurnOpsBar";
import { Badge } from "@/components/ui/Badge";
import {
  harvestKindLabel,
  isExecutionHarvestMessage,
} from "@/lib/executionHarvest";
import { cn, fmtInt, fmtMs, fmtTime } from "@/lib/utils";
import type {
  ReplayMessage,
  ReplayRun,
  ReplaySpan,
} from "@/services/adminObservability";
import { X } from "lucide-react";
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

export type ReplaySessionMeta = {
  title: string;
  deleted: boolean;
  userLabel: string;
  createdAt: string;
  conversationId: string;
  modelProfileName?: string | null;
  modelProfileId?: string | null;
  turns: number;
  errors: number;
  costLabel: string;
  multiAgentTurns: number;
};

export type InspectorActiveTab = "diagnosis" | string;

function runTabLabel(run: ReplayRun): string {
  return run.role || run.agent_id || "队员";
}

/**
 * Right dock: pinned diagnosis tab + closable worker tabs.
 * Mirrors the desktop side panel tab strip (without + / pop-out / cap).
 */
export function InspectorPanel({
  message,
  activeTab,
  workerTabIds,
  onActivateTab,
  onCloseWorkerTab,
  onSelectRun,
  onClose,
  cnyLabel,
  harvest,
  harvests = [],
  onSelectHarvest,
  session,
  hydrating = false,
  className,
}: {
  message: ReplayMessage | null;
  activeTab: InspectorActiveTab;
  workerTabIds: string[];
  onActivateTab: (tab: InspectorActiveTab) => void;
  onCloseWorkerTab: (runId: string) => void;
  onSelectRun: (runId: string) => void;
  onClose: () => void;
  /** Pre-formatted turn cost for ops strip, e.g. "¥0.12". */
  cnyLabel?: string | null;
  /** Preceding harvest, when the selected row is the assistant that followed it. */
  harvest?: ReplayMessage | null;
  harvests?: ReplayMessage[];
  onSelectHarvest?: (id: string) => void;
  session?: ReplaySessionMeta | null;
  /** Turn final-state fetch in flight — worker process may still be empty. */
  hydrating?: boolean;
  /** Height and width come from the page's layout row, not from the viewport. */
  className?: string;
}) {
  const runs = message?.runs ?? [];
  const spans = message?.spans ?? [];
  const runById = useMemo(() => {
    const map = new Map<string, ReplayRun>();
    for (const r of runs) map.set(r.run_id, r);
    return map;
  }, [runs]);
  const visibleWorkerTabIds = workerTabIds.filter((id) => runById.has(id));
  const activeRun =
    activeTab !== "diagnosis" ? (runById.get(activeTab) ?? null) : null;
  const showDiagnosis = activeTab === "diagnosis" || !activeRun;
  const selfHarvest = message ? isExecutionHarvestMessage(message) : false;
  const shownHarvest = selfHarvest ? message : (harvest ?? null);

  return (
    <aside
      className={cn(
        "flex flex-col gap-0 overflow-hidden bg-background",
        className,
      )}
    >
      <div className="flex shrink-0 items-center gap-1 border-border border-b px-2 py-1.5">
        <div
          role="tablist"
          aria-label="诊断面板"
          className="scrollbar-hidden flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto"
        >
          <DockTab
            id="diagnosis"
            label="诊断"
            selected={activeTab === "diagnosis"}
            onSelect={() => onActivateTab("diagnosis")}
          />
          {visibleWorkerTabIds.length > 0 && (
            <span
              aria-hidden
              className="mx-0.5 shrink-0 text-muted-foreground/60 text-xs"
            >
              |
            </span>
          )}
          {visibleWorkerTabIds.map((runId) => {
            const run = runById.get(runId);
            if (!run) return null;
            return (
              <DockTab
                key={runId}
                id={runId}
                label={runTabLabel(run)}
                selected={activeTab === runId}
                onSelect={() => onActivateTab(runId)}
                onClose={() => onCloseWorkerTab(runId)}
              />
            );
          })}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-md p-1 text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="关闭"
        >
          <X size={14} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3 space-y-4">
        {showDiagnosis ? (
          <DiagnosisBody
            message={message}
            selfHarvest={selfHarvest}
            shownHarvest={shownHarvest}
            spans={spans}
            runs={runs}
            cnyLabel={cnyLabel}
            harvests={harvests}
            onSelectHarvest={onSelectHarvest}
            session={session}
            listHighlightRunId={
              activeTab !== "diagnosis" ? activeTab : null
            }
            onSelectRun={onSelectRun}
          />
        ) : activeRun ? (
          <WorkerDetail
            message={message!}
            run={activeRun}
            spans={spans}
            hydrating={hydrating}
          />
        ) : null}
      </div>
    </aside>
  );
}

function DockTab({
  id,
  label,
  selected,
  onSelect,
  onClose,
}: {
  id: string;
  label: string;
  selected: boolean;
  onSelect: () => void;
  onClose?: () => void;
}) {
  return (
    <div
      role="tab"
      id={`inspector-tab-${id}`}
      aria-selected={selected}
      aria-controls={`inspector-tabpanel-${id}`}
      className={cn(
        "group flex shrink-0 items-center gap-0.5 rounded-md border px-2 py-1 text-xs outline-none transition-colors",
        selected
          ? "border-primary/40 bg-primary/10 text-foreground"
          : "border-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground",
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        className="max-w-[8rem] truncate outline-none focus-visible:underline"
      >
        {label}
      </button>
      {onClose && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          className="rounded p-0.5 text-muted-foreground outline-none opacity-0 transition-opacity hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100 focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`关闭 ${label}`}
        >
          <X size={12} />
        </button>
      )}
    </div>
  );
}

function DiagnosisBody({
  message,
  selfHarvest,
  shownHarvest,
  spans,
  runs,
  cnyLabel,
  harvests,
  onSelectHarvest,
  session,
  listHighlightRunId,
  onSelectRun,
}: {
  message: ReplayMessage | null;
  selfHarvest: boolean;
  shownHarvest: ReplayMessage | null;
  spans: ReplaySpan[];
  runs: ReplayRun[];
  cnyLabel?: string | null;
  harvests: ReplayMessage[];
  onSelectHarvest?: (id: string) => void;
  session?: ReplaySessionMeta | null;
  listHighlightRunId: string | null;
  onSelectRun: (runId: string) => void;
}) {
  return (
  <div
    role="tabpanel"
    id="inspector-tabpanel-diagnosis"
    aria-labelledby="inspector-tab-diagnosis"
    className="space-y-4"
  >
    {session && <SessionMeta session={session} />}
    {(harvests.length > 0 || message) && onSelectHarvest && (
      <TurnOpsBar
        selected={message}
        harvests={harvests}
        onSelectHarvest={onSelectHarvest}
      />
    )}
    {message && !selfHarvest && <OpsStrip message={message} cnyLabel={cnyLabel} />}
    {shownHarvest && (
      <HarvestBlock message={shownHarvest} asTrigger={!selfHarvest} />
    )}
    {message && !selfHarvest && spans.length > 0 && (
      <TurnSpanList message={message} />
    )}
    {message && !selfHarvest && (
      <WorkerList
        runs={runs}
        selectedRunId={listHighlightRunId}
        onSelectRun={onSelectRun}
      />
    )}
    {!message && (
      <p className="text-muted-foreground text-xs">
        点选一条助手消息查看该回合诊断。
      </p>
    )}
  </div>
  );
}

function SessionMeta({ session }: { session: ReplaySessionMeta }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-sm font-medium text-foreground">
          {session.title || "未命名会话"}
        </span>
        {session.deleted && <Badge tone="neutral">会话已删</Badge>}
      </div>
      <p className="text-muted-foreground text-xs">
        {session.userLabel}
        <span aria-hidden> · </span>
        <span className="tabular-nums">{fmtTime(session.createdAt)}</span>
      </p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground text-xs tabular-nums">
        <span>回合 {fmtInt(session.turns)}</span>
        <span>错误 {fmtInt(session.errors)}</span>
        <span>成本 {session.costLabel}</span>
        {session.multiAgentTurns > 0 && (
          <span>多 Agent {session.multiAgentTurns} 回合</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <CopyableId
          value={session.conversationId}
          label="conversation_id"
          display={session.conversationId.slice(0, 8)}
        />
        {session.modelProfileName && (
          <span
            className="text-muted-foreground text-xs"
            title={
              session.modelProfileId
                ? `profile ${session.modelProfileId}`
                : undefined
            }
          >
            {session.modelProfileName}
            {session.modelProfileId && (
              <span className="ml-1 font-mono">
                {session.modelProfileId.slice(0, 8)}
              </span>
            )}
          </span>
        )}
      </div>
    </div>
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

function WorkerList({
  runs,
  selectedRunId,
  onSelectRun,
}: {
  runs: ReplayRun[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
}) {
  if (runs.length === 0) {
    return (
      <p className="text-muted-foreground text-xs">
        本回合无多 Agent 委派。工具与模型调用见上方过程明细。
      </p>
    );
  }

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

function WorkerDetail({
  message,
  run,
  spans,
  hydrating,
}: {
  message: ReplayMessage;
  run: ReplayRun;
  spans: ReplaySpan[];
  hydrating: boolean;
}) {
  const body = runMessageBody(run);
  const turn = resolveChatTurn(chatTurnFromReplay(message));
  const process =
    turn.runs.find((r) => r.id === run.run_id)?.process ?? [];
  const runSpans = spans.filter((s) => s.run_id === run.run_id);
  const showSpanFallback = process.length === 0 && runSpans.length > 0;
  const processHasContent = process.some((s) => s.kind === "content");
  const bodyBlock = body ? (
    <div className="text-sm">
      <CollapsibleBody content={body} />
    </div>
  ) : null;

  return (
    <div
      role="tabpanel"
      id={`inspector-tabpanel-${run.run_id}`}
      aria-labelledby={`inspector-tab-${run.run_id}`}
      className="space-y-3"
    >
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

      {showSpanFallback && (
        <div className="space-y-1.5">
          {runSpans.map((span, i) =>
            span.kind === "tool" ? (
              <ToolLine key={`tool-${i}`} span={span} />
            ) : (
              <LlmProcessRow key={`llm-${i}`} span={span} />
            ),
          )}
        </div>
      )}

      {(process.length > 0 || (!processHasContent && bodyBlock)) && (
        <ProcessLane
          steps={process}
          collapse={false}
          hideContentSteps={false}
          fallbackContent={!processHasContent ? bodyBlock : null}
        />
      )}

      {hydrating && process.length === 0 && runSpans.length === 0 && (
        <p className="text-muted-foreground text-xs">正在加载队员过程…</p>
      )}

      {run.output_summary && !body && (
        <p className="text-sm text-muted-foreground">{run.output_summary}</p>
      )}

      {run.error && (
        <div className="rounded-lg bg-destructive/10 px-2.5 py-2 text-destructive text-xs">
          {run.error}
        </div>
      )}

      {!body &&
        !run.task &&
        !run.error &&
        process.length === 0 &&
        runSpans.length === 0 &&
        !hydrating && (
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
