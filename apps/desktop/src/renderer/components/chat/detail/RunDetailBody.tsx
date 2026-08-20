import { Markdown } from "@/components/chat/Markdown";
import { CollapsibleSpeech } from "@/components/chat/debate/CollapsibleSpeech";
import { processHasSuccessfulHandoff } from "@/components/chat/handoffBrief";
import { ProcessTimeline } from "@/components/chat/message-bubble/ProcessTimeline";
import { RunInterveneControls } from "@/components/graph/RunInterveneControls";
import {
  executionGraphCapabilities,
  runActCapabilities,
} from "@/components/graph/planCapabilities";
import { Button } from "@/components/ui";
import { useRunLlmWindow } from "@/hooks/useRunLlmWindow";
import { useTurnAudit } from "@/hooks/useTurnAudit";
import { filterInjectInEdges } from "@/lib/causalInject";
import type { AgentAuditEvent } from "@/services/audit";
import { permissionAxesShortLabel } from "@/services/permissionAxes";
import { activeRuntime, useConversationStore } from "@/stores/conversation";
import {
  type RunNode,
  revisionChains,
  useMessageExecution,
} from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import { turnDetailPath, useUIStore } from "@/stores/ui";
import { isLiveRunStatus } from "@agentcore/protocol-fold-kit";
import { Shield, Square } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { WorkerContextSection } from "./WorkerContextSection";
import {
  buildModeratorLedger,
  isDebateModeratorRun,
  isThinkingLivePlaceholder,
} from "./debateModerator";
import { receivedContextForList, selectRunTaskSection } from "./runTaskSection";
import { RunCausalInjectBlock } from "./sections/RunCausalInject";
import { DebriefSection } from "./sections/RunDebrief";
import { DiagnosticSection } from "./sections/RunDiagnostics";
import { EscalationSection } from "./sections/RunEscalations";
import { RunModeratorLedger } from "./sections/RunModeratorLedger";
import { RunOutcomeAcceptSection } from "./sections/RunOutcomeAccept";
import {
  RunRefGroup,
  SubtaskTree,
  countDescendants,
} from "./sections/RunRelations";
import { ResourceSection } from "./sections/RunResources";
import {
  RevisionChainSection,
  revisionComparePair,
} from "./sections/RunRevisionChain";
import { Section, StatusBadge } from "./sections/shared";

export { SchedulingDiag, CollabDiag } from "./sections/RunDiagnostics";

/**
 * 本回合生效的权限配方短名（原安全台账「本回合模式 · X」快照）。回合审计里
 * ``permission.axes_snapshot`` 在回合入口写一次；扫最后一条 permission 快照/切换的
 * ``permission_axes`` 即当时生效档。兼容旧 ``preset_*`` 行。
 */
function turnPresetSnapshot(
  events: AgentAuditEvent[] | null | undefined,
): string | null {
  let label: string | null = null;
  for (const e of events ?? []) {
    if (
      e.action === "permission.axes_snapshot" ||
      e.action === "permission.axes_changed" ||
      e.action === "permission.preset_snapshot" ||
      e.action === "permission.preset_changed"
    ) {
      const raw =
        e.detail?.permission_axes ?? e.detail?.permission_preset ?? null;
      // 短名仅经 permissionAxesShortLabel（含 JSON 字符串解析）；禁把整段 JSON 当芯片标签。
      const short = permissionAxesShortLabel(raw);
      if (short) label = short;
    }
  }
  return label;
}

/**
 * Single-run detail content — hybrid layout aligned with the CEO bubble timeline:
 * header anchors (role / status / live banner / task / moderator / revision /
 * escalation / context) → interleaved ProcessTimeline body → footer (debrief /
 * relations / resources / diagnostics).
 *
 * Bound to a specific message's execution slot (§9.3) via `messageId`, so the
 * conversation's right-side detail panel can pin a run from any turn (live or
 * historical) — the single home for run detail, reached from both the embedded
 * graph and the full-screen overlay. Chrome-free on purpose, so the drill-down
 * view is identical wherever it appears.
 */
export function RunDetailBody({
  messageId,
  runId,
}: {
  messageId: string;
  runId: string;
}) {
  const execution = useMessageExecution(messageId);
  const diagnosticMode = useUIStore((s) => s.diagnosticMode);
  const showRunDetail = useSidePanelStore((s) => s.showRunDetail);
  const navigate = useNavigate();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const traceId = useConversationStore(
    (s) =>
      activeRuntime(s).messages.find((m) => m.id === messageId)?.traceId ??
      null,
  );
  const turnCollab = useConversationStore(
    (s) =>
      activeRuntime(s).messages.find((m) => m.id === messageId)?.collab ?? null,
  );
  const turnInteractive = useConversationStore(
    (s) =>
      activeRuntime(s).messages.find((m) => m.id === messageId)?.isStreaming ??
      false,
  );

  const run = execution?.runs.find((s) => s.id === runId);
  const agent = run
    ? execution?.agents.find((a) => a.id === run.agentId)
    : null;
  const graphCaps = executionGraphCapabilities(execution);
  const runCaps = runActCapabilities(execution, runId);
  const turnAudit = useTurnAudit(
    conversationId != null ? conversationId : null,
    messageId,
  );
  const llmWindow = useRunLlmWindow(
    conversationId,
    messageId,
    runId,
    diagnosticMode,
  );

  if (!execution || !run || !agent) return null;

  const output = agent.outputChunks.join("");
  // 整轮停只给 captain：队员栏再夹一枚方块停止，会和「停止这位队员」看起来像同一件事。
  // 整轮硬停的主入口是输入框。「跑完再说」走输入框排队，不在右坞再放填草稿入口。
  const isCaptainRun = run.kind === "captain";
  const working = agent.status === "working";
  const stopTurnAction =
    working && isCaptainRun ? (
      <Button
        variant="ghost"
        className="h-7 text-destructive hover:bg-destructive/10"
        icon={<Square size={13} />}
        onClick={() => useConversationStore.getState().stopGeneration()}
      >
        停止整轮
      </Button>
    ) : null;
  // 按人干预（只改这个人 / 只停这个人）：只在 `isLiveRunStatus` 时挂载（running /
  // pending）。终局整条不渲染、也不写灰字原因——死按钮没有教学价值。排队仍画：可停；
  // 改方向继续变灰 +「还没开工」。
  //
  // captain 除外（手机早有这道护栏）：主管这一路就是这条对话本身，「只停这位队员」对它
  // 无意义，引擎的计划里也没有它——出了按钮就是许一个必然落空的愿。要停就停整轮。
  const intervene =
    conversationId != null && !isCaptainRun && isLiveRunStatus(run.status) ? (
      <RunInterveneControls
        conversationId={conversationId}
        executionId={execution.id}
        runId={run.id}
        runStatus={run.status}
        role={agent.role}
        redirectCapable={runCaps.runRedirect}
        output={output}
      />
    ) : (
      stopTurnAction
    );
  const thinkingLive = isThinkingLivePlaceholder(agent);

  const isModerator = isDebateModeratorRun(execution, run.id);
  const moderatorLedger = isModerator ? buildModeratorLedger(execution) : null;
  const upstream = run.dependsOn
    .map((id) => execution.runs.find((r) => r.id === id))
    .filter((r): r is RunNode => r != null);
  const downstream = execution.runs.filter((r) => r.dependsOn.includes(run.id));
  const parent =
    run.parentRunId != null && run.continuesRunId == null
      ? (execution.runs.find((r) => r.id === run.parentRunId) ?? null)
      : null;
  const childCount = countDescendants(execution.runs, run.id);
  const hasInjectIn =
    graphCaps.auditInject &&
    filterInjectInEdges(turnAudit.data?.causal_graph, run.id).length > 0;
  const chain =
    revisionChains(execution).find((c) =>
      c.versions.some((v) => v.run.id === run.id),
    ) ?? null;
  const taskSection = selectRunTaskSection(run);
  const contextBlocks = receivedContextForList(
    run.receivedContext,
    taskSection.promotedTask,
  );

  const rawTurnPreset = turnPresetSnapshot(turnAudit.data?.data);
  const turnPresetLabel = rawTurnPreset;

  const process = run.process;
  const showTimeline =
    process.length > 0 ||
    thinkingLive ||
    (agent.toolProgress != null && agent.status === "working") ||
    (agent.status === "working" && !isModerator);

  return (
    <div className="p-4">
      <div className="mb-4 flex items-center gap-2">
        <span className="flex-1 truncate text-sm font-medium text-foreground">
          {agent.role}
        </span>
        {turnPresetLabel && (
          <span
            title="本回合生效的权限模式"
            className="inline-flex shrink-0 items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-foreground"
          >
            <Shield size={11} className="shrink-0" />
            {turnPresetLabel}
          </span>
        )}
        <StatusBadge
          status={run.status}
          phase={run.phase}
          phaseTool={run.phaseTool}
        />
        {run.durationMs != null && (
          <span className="shrink-0 text-xs text-muted-foreground">
            {(run.durationMs / 1000).toFixed(1)}s
          </span>
        )}
      </div>

      {agent.status === "working" && (
        <div className="mb-4 space-y-2 rounded-xl border border-primary/20 bg-primary/5 px-3 py-2.5 text-xs">
          <p className="text-sm text-foreground">
            {run.continuesRunId != null
              ? "同一人接续中——带着现场按新指令接着干。"
              : run.replacesRunId != null
                ? "接手重写——同角色新人按新方向重做。"
                : isModerator
                  ? "辩论主持中——下方台账会随轮次更新焦点与小结。"
                  : "正在实时输出——下方内容会边写边更新。"}
          </p>
          {intervene}
        </div>
      )}

      {/* 队员不在跑、但仍 live（排队 pending）时露出按人干预。终局 `intervene` 为
          null，这里整块不挂，避免空 wrapper。 */}
      {agent.status !== "working" && intervene && (
        <div className="mb-4">{intervene}</div>
      )}

      <Section title={taskSection.title}>
        <CollapsibleSpeech
          contentKey={taskSection.body}
          fadeToClass="from-card"
          sceneKey={`run:${runId}:task`}
        >
          <Markdown content={taskSection.body} />
        </CollapsibleSpeech>
      </Section>

      {moderatorLedger && (
        <RunModeratorLedger
          ledger={moderatorLedger}
          onOpenDebateRoom={
            conversationId
              ? () => {
                  navigate(turnDetailPath(conversationId, messageId, "debate"));
                }
              : undefined
          }
        />
      )}

      {chain && (
        <RevisionChainSection
          chain={chain}
          currentRunId={run.id}
          agents={execution.agents}
          execution={execution}
          onSelect={(rid, role) => showRunDetail(messageId, rid, role)}
          onCompare={
            conversationId
              ? () => {
                  navigate(
                    turnDetailPath(
                      conversationId,
                      messageId,
                      "compare",
                      revisionComparePair(chain, run.id),
                    ),
                  );
                }
              : undefined
          }
        />
      )}

      {run.escalations.length > 0 && (
        <EscalationSection
          run={run}
          role={agent.role}
          conversationId={conversationId}
          interactive={turnInteractive}
        />
      )}

      {(contextBlocks.length > 0 ||
        (diagnosticMode && conversationId != null)) && (
        <WorkerContextSection
          blocks={contextBlocks}
          diagnosticMode={diagnosticMode && conversationId != null}
          diagnostic={{
            messages: llmWindow.data?.messages ?? [],
            available: llmWindow.data?.available ?? false,
            loading: llmWindow.loading,
            error: llmWindow.error,
          }}
          keyBase={`run:${runId}`}
          onNavigate={(rid) => {
            const target = execution.runs.find((r) => r.id === rid);
            if (!target) return;
            const role = execution.agents.find(
              (a) => a.id === target.agentId,
            )?.role;
            showRunDetail(messageId, rid, role);
          }}
        />
      )}

      {run.error && (
        <Section title="错误">
          <p className="whitespace-pre-wrap break-words text-xs text-destructive">
            {run.error}
          </p>
        </Section>
      )}

      {/* 跑一半改方向 · 忽略路径收口 (Step 4): a terminal run whose「改方向」steer couldn't apply or
          whose failure is non-retryable — surface it + let the user record an explicit accept.
          Gated to terminal runs so an in-flight run never triggers the audit read. */}
      {conversationId != null &&
        run.status !== "pending" &&
        run.status !== "running" && (
          <RunOutcomeAcceptSection
            conversationId={conversationId}
            messageId={messageId}
            runId={runId}
          />
        )}

      {showTimeline && (
        <div className="mb-4">
          <ProcessTimeline
            process={process}
            isStreaming={agent.status === "working"}
            citations={[]}
            composingTool={
              agent.status === "working" ? agent.toolProgress : null
            }
            fallbackContent=""
            messageId={`${messageId}:${runId}`}
            conversationId={conversationId}
            checkpoints={[]}
            planReviews={[]}
            teamPreviews={[]}
            collapseProcessSteps={false}
          />
        </div>
      )}

      {run.debrief && !processHasSuccessfulHandoff(process) ? (
        <DebriefSection debrief={run.debrief} />
      ) : run.outputSummary && !run.debrief ? (
        <Section title="结论">
          <Markdown content={run.outputSummary} />
        </Section>
      ) : null}

      {(upstream.length > 0 ||
        downstream.length > 0 ||
        parent ||
        childCount > 0 ||
        hasInjectIn) && (
        <Section title="关系">
          <div className="space-y-3">
            {upstream.length > 0 && (
              <RunRefGroup
                label="依赖"
                runs={upstream}
                agents={execution.agents}
                onSelect={(rid, role) => showRunDetail(messageId, rid, role)}
              />
            )}
            {downstream.length > 0 && (
              <RunRefGroup
                label="后续"
                runs={downstream}
                agents={execution.agents}
                onSelect={(rid, role) => showRunDetail(messageId, rid, role)}
              />
            )}
            {parent && (
              <RunRefGroup
                label="上级"
                runs={[parent]}
                agents={execution.agents}
                onSelect={(rid, role) => showRunDetail(messageId, rid, role)}
              />
            )}
            {childCount > 0 && (
              <div>
                <p className="mb-1 text-xs text-muted-foreground">
                  子任务（{childCount}）
                </p>
                <SubtaskTree
                  parentId={run.id}
                  runs={execution.runs}
                  agents={execution.agents}
                  depth={0}
                  onSelect={(rid, role) => showRunDetail(messageId, rid, role)}
                />
              </div>
            )}
            {hasInjectIn && (
              <RunCausalInjectBlock
                runId={run.id}
                graph={turnAudit.data?.causal_graph}
                runs={execution.runs}
                agents={execution.agents}
                onSelect={(rid, role) => showRunDetail(messageId, rid, role)}
                sceneKey={`run:${runId}:causal-inject`}
              />
            )}
          </div>
        </Section>
      )}

      {(run.usage || run.cost) && (
        <ResourceSection
          run={run}
          agent={agent}
          defaultExpanded
          keyBase={`run:${runId}`}
        />
      )}

      {diagnosticMode && (
        <DiagnosticSection
          run={run}
          executionId={execution.id}
          traceId={traceId}
          batches={execution.batches}
          collab={turnCollab}
          keyBase={`run:${runId}`}
        />
      )}
    </div>
  );
}
