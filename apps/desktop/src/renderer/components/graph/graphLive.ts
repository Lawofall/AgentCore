/**
 * Collaboration-graph Live layer — derived selectors over `execution@playhead`.
 *
 * No writable GraphLive table: faces / edges subscribe to the projected Execution
 * (or Inject paint context) by id. Document shells stay referentially stable while
 * streaming deltas only re-render the subscribed face.
 */

import {
  challengePreviewFromContext,
  debateFacePrimaryFromContext,
} from "@/components/chat/debate/debateFaceCopy";
import {
  captainSynthesisPreviewText,
  coordinationWaitCaptainCaption,
  waitingWorkerRoles,
} from "@/components/chat/teamSynthesisPhase";
import type { InjectGraphOverlay } from "@/lib/causalInject";
import {
  chunksTailText,
  estimateTokensFromCharCount,
  formatCostCaption,
  headText,
  pickCostMoney,
  sumChunkChars,
} from "@/lib/format";
import { detectReviewConcern, isReviewLikeWorker } from "@/lib/reviewConcern";
import { isGraphPerfEnabled, markGraphPerf } from "@/services/graphPerf";
import {
  type AgentState,
  type Execution,
  type RunNode,
  type RunStatus,
  debateBeatFromContext,
  execRuntime,
  projectRuntime,
  useActiveExecField,
  useExecutionScope,
  useExecutionStore,
} from "@/stores/execution";
import { createContext, useCallback, useContext, useMemo } from "react";
import type { ActSummaryData } from "./ActSummaryNode";
import {
  type AgentNodeData,
  isDebateAgentNode,
  pickEscalationKind,
  revisionFeedbackSummary,
} from "./agentNode/shared";
import { INPUT_ID } from "./constants";
import { useGraphActions } from "./graphActions";
import {
  aggregateDebateRoundStatus,
  captainSinkPreview,
  debateRoundActiveBeat,
  debateRoundPhaseLabel,
  debateRoundSettledMark,
  deriveArtifacts,
  deriveCaptainStatus,
  pickDebateCrossExamActivateId,
  workerRunsOf,
} from "./helpers";
import { stripNamespace } from "./ids";
import { type GraphScene, buildGraphScene } from "./scene";

const EMPTY_FOLDED: readonly string[] = [];
/** Stream: scan review concern every N output chars; terminal statuses always scan. */
const REVIEW_CONCERN_MILESTONE = 256;
/** When true, node/edge faces self-read Live; Document shells omit live fields. */
export const GraphDocumentModeContext = createContext(false);

export function useGraphDocumentMode(): boolean {
  return useContext(GraphDocumentModeContext);
}

/** Inject highlight / dim paint — Live overlay; never baked into Document edges. */
export type GraphInjectPaint = {
  highlightEdgeIds: ReadonlySet<string>;
  focusedEdgeIds: ReadonlySet<string>;
  dimUnrelatedEdges: boolean;
} | null;

export const GraphInjectPaintContext = createContext<GraphInjectPaint>(null);

export function useGraphInjectPaint(): GraphInjectPaint {
  return useContext(GraphInjectPaintContext);
}

/** Final-answer text for captain preview (drill-in); not on Document shells. */
export const GraphCaptainAnswerContext = createContext<{
  content: string;
} | null>(null);

/** Scene topology for Live face derivation (same lifetime as Document). */
export const GraphSceneContext = createContext<GraphScene | null>(null);

export function useGraphScene(): GraphScene | null {
  return useContext(GraphSceneContext);
}

function sumDurationMs(
  runs: readonly { durationMs?: number | null }[],
): number | null {
  let sum = 0;
  let any = false;
  for (const r of runs) {
    if (r.durationMs != null && r.durationMs > 0) {
      sum += r.durationMs;
      any = true;
    }
  }
  return any ? sum : null;
}

/** Cheap chunk meta for live signatures — never joins text. */
function chunkMetaSig(chunks: readonly string[]): string {
  const n = chunks.length;
  if (n === 0) return "0:0";
  const last = chunks[n - 1];
  return `${n}:${last?.length ?? 0}`;
}

function toolProgressSig(
  tp: AgentState["toolProgress"] | null | undefined,
): string {
  return tp ? `${tp.toolName}:${tp.chars}` : "";
}

function toolExecLiveSig(
  te: AgentState["toolExecutionLive"] | null | undefined,
): string {
  return te ? `${te.toolName}:${te.phase}` : "";
}

function escalationCountSig(run: RunNode): string {
  let pending = 0;
  let raised = 0;
  for (const e of run.escalations) {
    if (e.status === "pending") pending++;
    else if (e.status === "raised") raised++;
  }
  return `${pending}:${raised}:${run.escalations.length}`;
}

/** tool_use_end mutates status in place — length alone misses completion. */
function toolCallsSig(toolCalls: AgentState["toolCalls"] | undefined): string {
  if (!toolCalls || toolCalls.length === 0) return "0";
  let running = 0;
  let success = 0;
  let error = 0;
  for (const t of toolCalls) {
    if (t.status === "running") running++;
    else if (t.status === "success") success++;
    else error++;
  }
  return `${toolCalls.length}:${running}:${success}:${error}`;
}

function agentFaceSig(agent: AgentState | undefined): string {
  if (!agent) return "";
  return [
    chunkMetaSig(agent.outputChunks),
    chunkMetaSig(agent.reasoningChunks),
    toolProgressSig(agent.toolProgress),
    toolExecLiveSig(agent.toolExecutionLive),
    toolCallsSig(agent.toolCalls),
  ].join("|");
}

function runFaceSig(run: RunNode): string {
  const usage = run.usage;
  const cost = pickCostMoney(run.cost);
  return [
    run.status,
    run.phase ?? "",
    run.phaseTool ?? "",
    run.durationMs ?? "",
    run.startedAt ?? "",
    run.model ?? "",
    run.error ? "1" : "0",
    run.failureKind ?? "",
    usage ? `${usage.input}+${usage.output}` : "",
    cost ? `${cost.nano}:${cost.estimated ? 1 : 0}:${cost.currency}` : "",
    run.checkpoint?.status ?? "",
    escalationCountSig(run),
  ].join("|");
}

/**
 * Per-agent Live signature — stable under other agents' delta floods.
 * Includes host + folded debate beats' status/chunk meta (no full-text join).
 */
export function agentNodeLiveSig(
  execution: Execution | null,
  runId: string,
  foldedRunIds: readonly string[] = EMPTY_FOLDED,
): string {
  if (!execution) return "";
  const run = execution.runs.find((r) => r.id === runId);
  if (!run) return `missing:${runId}`;
  const round: RunNode[] = [run];
  for (const id of foldedRunIds) {
    const fr = execution.runs.find((r) => r.id === id);
    if (fr) round.push(fr);
  }
  const parts: string[] = [];
  for (const r of round) {
    const agent = execution.agents.find((a) => a.id === r.agentId);
    parts.push(`${r.id}:${runFaceSig(r)}:${agentFaceSig(agent)}`);
  }
  return parts.join(";");
}

/** Captain sink Live signature — worker status transitions, not chunk deltas. */
export function captainEndpointLiveSig(
  execution: Execution | null,
  captainRunId: string,
  turnTerminal: boolean,
  detached = false,
): string {
  if (!execution) return "";
  const cap = deriveCaptainStatus(execution, captainRunId, {
    turnTerminal,
    detached,
  });
  const workers = execution.runs
    .filter((r) => r.id !== captainRunId)
    .map((r) => `${r.id}:${r.status}`)
    .join(",");
  return `${execution.status}|${cap}|${turnTerminal ? 1 : 0}|${detached ? 1 : 0}|${workers}`;
}

/** Act card Live signature — status / decisions / duration, not stream text. */
export function actSummaryLiveSig(execution: Execution | null): string {
  if (!execution) return "";
  const bits = execution.runs.map((r) => {
    return `${r.status}:${escalationCountSig(r)}:${r.checkpoint?.status ?? ""}:${r.durationMs ?? ""}`;
  });
  return `${execution.status}|${bits.join(",")}`;
}

/** Step-edge animated Live signature. */
export function stepEdgeAnimatedSig(
  execution: Execution | null,
  bareTarget: string,
  captainRunId: string | null,
  turnTerminal: boolean,
  detached = false,
): string {
  if (!execution) return "0";
  if (captainRunId && bareTarget === captainRunId) {
    return deriveCaptainStatus(execution, captainRunId, {
      turnTerminal,
      detached,
    }) === "running"
      ? "1"
      : "0";
  }
  return execution.runs.find((s) => s.id === bareTarget)?.status === "running"
    ? "1"
    : "0";
}

function reviewConcernForFace(
  agent: AgentState | undefined,
  faceRun: RunNode,
  status: RunStatus,
): ReturnType<typeof detectReviewConcern> {
  const role = agent?.role ?? faceRun.role ?? "";
  if (!isReviewLikeWorker(role, faceRun.id)) return null;
  const chunks = agent?.outputChunks ?? [];
  const charLen = sumChunkChars(chunks);
  if (charLen < 12) return null;
  if (status === "running") {
    const last = chunks.length > 0 ? chunks[chunks.length - 1] : undefined;
    const prevLen = charLen - (last?.length ?? 0);
    const crossed =
      Math.floor(prevLen / REVIEW_CONCERN_MILESTONE) !==
      Math.floor(charLen / REVIEW_CONCERN_MILESTONE);
    const firstHit = prevLen < 12;
    if (!crossed && !firstHit) return null;
  }
  // Terminal (or milestone): one join — not per-delta on the hot path.
  return detectReviewConcern(chunks.join(""), {
    role,
    runId: faceRun.id,
  });
}

function useLiveExecutionGetter(): () => Execution | null {
  const messageId = useExecutionScope();
  return useCallback(() => {
    const rt = execRuntime(useExecutionStore.getState(), messageId);
    return projectRuntime(rt);
  }, [messageId]);
}

/**
 * Live face fields for one agent (or debate-round host) run.
 * Pure over Execution + scene beat folds — safe for hooks / tests.
 */
export function deriveAgentNodeLive(
  execution: Execution,
  run: RunNode,
  opts: {
    scene: GraphScene | null;
    litRunId: string | null;
    enterIndex: number;
    unitExpanded: boolean;
    nodeWidth?: number;
    handleDirection?: "vertical" | "horizontal";
    activateNode?: (id: string) => void;
    toggleUnitExpand?: (unitId: string) => void;
  },
): AgentNodeData {
  const t0 = isGraphPerfEnabled() ? performance.now() : 0;
  const workerIdSet = new Set(workerRunsOf(execution.runs).map((r) => r.id));
  const runById = new Map(execution.runs.map((r) => [r.id, r]));
  const foldInfo = opts.scene?.fold;
  const foldedCx = (opts.scene?.beatFoldsByHost.get(run.id) ?? [])
    .map((id) => runById.get(id))
    .filter((r): r is RunNode => r != null);
  const roundRuns = foldedCx.length > 0 ? [run, ...foldedCx] : [run];
  const aggregatedStatus: RunStatus =
    foldedCx.length > 0
      ? aggregateDebateRoundStatus(roundRuns.map((r) => r.status))
      : run.status;
  const activeBeat =
    foldedCx.length > 0
      ? debateRoundActiveBeat(
          run.status,
          foldedCx.map((r) => r.status),
        )
      : "statement";
  const phaseLabel = debateRoundPhaseLabel(
    aggregatedStatus,
    activeBeat,
    foldedCx.length > 0,
  );
  const faceRun =
    activeBeat === "cross_exam"
      ? (foldedCx.find((r) => r.status === "running") ??
        foldedCx[foldedCx.length - 1] ??
        run)
      : run;
  const agent = execution.agents.find((a) => a.id === faceRun.agentId);
  const hostAgent = execution.agents.find((a) => a.id === run.agentId);
  const outputChunks = agent?.outputChunks ?? [];
  const reasoningChunks = agent?.reasoningChunks ?? [];
  const outputChars = sumChunkChars(outputChunks);
  const reviewConcern = reviewConcernForFace(agent, faceRun, aggregatedStatus);
  const focused =
    opts.litRunId === run.id || foldedCx.some((r) => r.id === opts.litRunId);
  const isContinuation = run.continuesRunId != null;
  const isSubtask =
    !isContinuation &&
    !!run.parentRunId &&
    run.parentRunId !== run.id &&
    workerIdSet.has(run.parentRunId);
  const foldedChildCount = foldInfo?.descendants.get(run.id)?.length ?? 0;
  const durationMs =
    foldedCx.length > 0 ? sumDurationMs(roundRuns) : run.durationMs;
  // 无 FX：同一节点内各 run 同凭据来源 → 同价卡表 → 同币种，按首个记名。
  let costNano = 0;
  let costEstimated = false;
  let costCurrency: string | null = null;
  for (const r of roundRuns) {
    const m = pickCostMoney(r.cost);
    if (!m || m.nano <= 0) continue;
    costNano += m.nano;
    if (m.estimated) costEstimated = true;
    costCurrency ??= m.currency;
  }
  const realTokens = roundRuns.reduce(
    (n, r) => n + (r.usage ? r.usage.input + r.usage.output : 0),
    0,
  );
  const activateId =
    aggregatedStatus === "running" && faceRun.id !== run.id
      ? faceRun.id
      : run.id;
  const cxActivateId =
    foldedCx.length > 0 ? pickDebateCrossExamActivateId(foldedCx) : null;
  const settledMark =
    foldedCx.length > 0
      ? debateRoundSettledMark(
          aggregatedStatus,
          true,
          foldedCx.map((r) => r.status),
          foldedCx.map((r) => debateBeatFromContext(r.receivedContext)),
        )
      : null;
  const activate = opts.activateNode;
  const toggle = opts.toggleUnitExpand;

  const live: AgentNodeData = {
    agentId: run.agentId,
    role: (hostAgent ?? agent)?.role ?? run.agentId,
    runId: run.id,
    status: aggregatedStatus,
    isAnimating: aggregatedStatus === "running",
    task: run.task,
    error:
      aggregatedStatus === "failed"
        ? (roundRuns.find((r) => r.status === "failed")?.error ?? run.error)
        : run.error,
    failureKind:
      aggregatedStatus === "failed"
        ? (roundRuns.find((r) => r.status === "failed")?.failureKind ??
          run.failureKind)
        : run.failureKind,
    productLanded:
      aggregatedStatus === "failed"
        ? (roundRuns.find((r) => r.status === "failed")?.productLanded ??
          run.productLanded)
        : run.productLanded,
    outputPreview: chunksTailText(outputChunks),
    debateFacePrimary: isDebateAgentNode({
      stance: run.stance,
      group: run.group,
    })
      ? debateFacePrimaryFromContext(run.receivedContext)
      : null,
    challengePreview: isDebateAgentNode({
      stance: run.stance,
      group: run.group,
    })
      ? challengePreviewFromContext(run.receivedContext)
      : null,
    reasoningPreview: chunksTailText(reasoningChunks),
    toolProgress: agent?.toolProgress ?? null,
    toolExecutionLive: agent?.toolExecutionLive ?? null,
    phase: faceRun.phase ?? run.phase ?? null,
    phaseTool: faceRun.phaseTool ?? run.phaseTool ?? null,
    tokenCount: estimateTokensFromCharCount(outputChars),
    toolCount: agent?.toolCalls.length ?? 0,
    artifacts: agent ? deriveArtifacts(agent.toolCalls) : [],
    focused,
    nodeWidth: opts.nodeWidth,
    model: faceRun.model ?? run.model,
    durationMs,
    startedAt: faceRun.startedAt ?? run.startedAt,
    realTokens,
    costText:
      costNano > 0
        ? formatCostCaption(costNano, costEstimated, costCurrency)
        : undefined,
    handleDirection: opts.handleDirection,
    isSubtask,
    isRevision: isContinuation,
    continuationIndex: run.continuationIndex,
    continuesRunId: run.continuesRunId,
    round: run.round,
    debateBeat: isContinuation
      ? debateBeatFromContext(run.receivedContext)
      : null,
    debateRoundPhase: phaseLabel,
    debateCrossExamMark: settledMark,
    onActivateCrossExam:
      settledMark && cxActivateId && activate
        ? () => activate(cxActivateId)
        : undefined,
    group: run.group,
    revisionSummary: isContinuation
      ? revisionFeedbackSummary(run.receivedContext)
      : null,
    revised: run.revised,
    replacesRunId: run.replacesRunId,
    stance: run.stance,
    checkpoint: run.checkpoint,
    escalationPending: roundRuns.reduce(
      (n, r) => n + r.escalations.filter((e) => e.status === "pending").length,
      0,
    ),
    escalationRaised: roundRuns.reduce(
      (n, r) => n + r.escalations.filter((e) => e.status === "raised").length,
      0,
    ),
    escalationKind: pickEscalationKind(roundRuns.flatMap((r) => r.escalations)),
    reviewConcern,
    foldedChildCount:
      foldedChildCount > 0 && !foldInfo?.debateUnits.has(run.id)
        ? foldedChildCount
        : undefined,
    unitExpanded: opts.unitExpanded,
    onToggleUnitExpand: toggle ? () => toggle(run.id) : undefined,
    enterIndex: opts.enterIndex,
    onActivate: activate ? () => activate(activateId) : undefined,
  };
  if (isGraphPerfEnabled()) {
    markGraphPerf("liveFace", performance.now() - t0, { runId: run.id });
  }
  return live;
}

/** Document shell whitelist for agent nodes (identity / direction only). */
export type AgentNodeShell = {
  agentId: string;
  role: string;
  runId: string;
  task: string;
  handleDirection?: "vertical" | "horizontal";
  isSubtask?: boolean;
  isRevision?: boolean;
  continuationIndex?: number;
  continuesRunId?: string | null;
  round?: number;
  group?: string | null;
  stance?: AgentNodeData["stance"];
  replacesRunId?: string | null;
  revised?: AgentNodeData["revised"];
  enterIndex?: number;
  nodeWidth?: number;
  unitExpanded?: boolean;
  foldedChildCount?: number;
  debateBeat?: AgentNodeData["debateBeat"];
};

export function agentNodeToShell(d: AgentNodeData): AgentNodeShell {
  return {
    agentId: d.agentId,
    role: d.role,
    runId: d.runId,
    task: d.task,
    handleDirection: d.handleDirection,
    isSubtask: d.isSubtask,
    isRevision: d.isRevision,
    continuationIndex: d.continuationIndex,
    continuesRunId: d.continuesRunId,
    round: d.round,
    group: d.group,
    stance: d.stance,
    replacesRunId: d.replacesRunId,
    revised: d.revised,
    enterIndex: d.enterIndex,
    nodeWidth: d.nodeWidth,
    unitExpanded: d.unitExpanded,
    foldedChildCount: d.foldedChildCount,
    debateBeat: d.debateBeat,
  };
}

function pendingShell(shell: AgentNodeShell): AgentNodeData {
  return {
    ...shell,
    status: "pending",
    isAnimating: false,
    outputPreview: "",
    tokenCount: 0,
    toolCount: 0,
    focused: false,
  } as AgentNodeData;
}

/**
 * Subscribe to Live face for an agent Document shell.
 * Returns full AgentNodeData merged from shell identity + execution@playhead.
 */
export function useAgentNodeLive(shell: AgentNodeShell): AgentNodeData {
  const actions = useGraphActions();
  const scene = useGraphScene();
  const foldedRunIds = scene?.beatFoldsByHost.get(shell.runId) ?? EMPTY_FOLDED;
  const liveSig = useActiveExecField((rt) =>
    agentNodeLiveSig(projectRuntime(rt), shell.runId, foldedRunIds),
  );
  const getExecution = useLiveExecutionGetter();
  // biome-ignore lint/correctness/useExhaustiveDependencies: liveSig is intentional invalidation key (getExecution reads fresh)
  return useMemo(() => {
    const execution = getExecution();
    if (!execution) return pendingShell(shell);
    const run = execution.runs.find((r) => r.id === shell.runId);
    if (!run) return pendingShell(shell);
    return deriveAgentNodeLive(execution, run, {
      scene,
      litRunId: actions.litRunId,
      enterIndex: shell.enterIndex ?? 0,
      unitExpanded: shell.unitExpanded ?? false,
      nodeWidth: shell.nodeWidth,
      handleDirection: shell.handleDirection,
      activateNode: actions.activateNode,
      toggleUnitExpand: actions.toggleUnitExpand,
    });
  }, [liveSig, shell, scene, actions, getExecution]);
}

export type EndpointLive = {
  status: RunStatus;
  preview?: string;
  statusCaption?: string;
  focused: boolean;
  label: string;
  onActivate?: () => void;
};

export function useInputEndpointLive(labelFromShell: string): EndpointLive {
  const actions = useGraphActions();
  const taskSummary = useActiveExecField((rt) => rt.plan?.taskSummary ?? "");
  return useMemo(() => {
    const label = labelFromShell || taskSummary || "";
    return {
      status: "completed" as RunStatus,
      label,
      focused:
        !!actions.taskMessageId &&
        actions.litEndpointMessageId === actions.taskMessageId,
      onActivate: actions.taskMessageId
        ? () => actions.activateNode(INPUT_ID)
        : undefined,
    };
  }, [labelFromShell, taskSummary, actions]);
}

export function useCaptainEndpointLive(runId: string): EndpointLive {
  const actions = useGraphActions();
  const answer = useContext(GraphCaptainAnswerContext);
  const liveSig = useActiveExecField((rt) => {
    const exec = projectRuntime(rt);
    const detached = rt.executionDetached != null;
    const base = captainEndpointLiveSig(
      exec,
      runId,
      actions.turnTerminal,
      detached,
    );
    const preview = rt.teamSynthesisPreview;
    const previewSig = preview
      ? `${preview.headline.length}:${preview.text.length}:${preview.workers.length}`
      : "";
    const w = rt.coordinationWait;
    const waitSig = w ? `${w.completed}/${w.total}` : "";
    return `${base}|${previewSig}|${waitSig}`;
  });
  const wait = useActiveExecField((rt) => rt.coordinationWait);
  const detached = Boolean(useActiveExecField((rt) => rt.executionDetached));
  const teamSynthesisPreview = useActiveExecField(
    (rt) => rt.teamSynthesisPreview,
  );
  const getExecution = useLiveExecutionGetter();

  // biome-ignore lint/correctness/useExhaustiveDependencies: liveSig is intentional invalidation key (getExecution reads fresh)
  return useMemo(() => {
    const execution = getExecution();
    const captainStatus = execution
      ? deriveCaptainStatus(execution, runId, {
          turnTerminal: actions.turnTerminal,
          detached,
        })
      : ("pending" as RunStatus);
    const waitingRoles = execution ? waitingWorkerRoles(execution) : [];
    const waitCaption = (
      coordinationWaitCaptainCaption(wait, { waitingRoles }) ?? ""
    ).trim();
    const sinkStatus: RunStatus =
      waitCaption && !detached ? "running" : captainStatus;
    const preview = captainSinkPreview({
      captainStatus,
      answerPreview: answer?.content ? headText(answer.content) : "",
      synthesisPreview: captainSynthesisPreviewText(teamSynthesisPreview),
      waitCaption,
    });
    return {
      status: sinkStatus,
      statusCaption: waitCaption || undefined,
      label: "",
      preview,
      focused:
        !!actions.finalAnswerId &&
        actions.litEndpointMessageId === actions.finalAnswerId,
      onActivate: actions.finalAnswerId
        ? () => actions.activateNode(runId)
        : undefined,
    };
  }, [
    liveSig,
    runId,
    actions,
    answer,
    wait,
    detached,
    teamSynthesisPreview,
    getExecution,
  ]);
}

export type ActSummaryLive = Pick<
  ActSummaryData,
  | "status"
  | "roles"
  | "agentCount"
  | "completed"
  | "total"
  | "durationMs"
  | "pendingDecisions"
>;

/** Live act-card progress — derived from current Execution via scene IR. */
export function useActSummaryLive(actId: string): ActSummaryLive | null {
  const liveSig = useActiveExecField((rt) =>
    actSummaryLiveSig(projectRuntime(rt)),
  );
  const getExecution = useLiveExecutionGetter();
  // biome-ignore lint/correctness/useExhaustiveDependencies: liveSig is intentional invalidation key (getExecution reads fresh)
  return useMemo(() => {
    const execution = getExecution();
    if (!execution) return null;
    const scene = buildGraphScene(execution, { inputId: INPUT_ID });
    const sa = scene.acts.find((a) => a.actId === actId);
    if (!sa) return null;
    return {
      status: sa.status,
      roles: sa.roles,
      agentCount: sa.agentCount,
      completed: sa.completed,
      total: sa.total,
      durationMs: sa.durationMs,
      pendingDecisions: sa.pendingDecisions,
    };
  }, [liveSig, actId, getExecution]);
}

/** Edge animated? — Live read of target run / captain status. */
export function useStepEdgeAnimated(
  targetId: string,
  captainRunId: string | null,
): boolean {
  const documentMode = useGraphDocumentMode();
  const { turnTerminal } = useGraphActions();
  // Canvas namespaces RF edge endpoints as `turnId::bare`; Live looks up bare run ids.
  const bareTarget = stripNamespace(targetId);
  const liveSig = useActiveExecField((rt) => {
    if (!documentMode) return "0";
    return stepEdgeAnimatedSig(
      projectRuntime(rt),
      bareTarget,
      captainRunId,
      turnTerminal,
      rt.executionDetached != null,
    );
  });
  return liveSig === "1";
}

export function injectPaintFromOverlay(
  overlay: InjectGraphOverlay | null | undefined,
): GraphInjectPaint {
  if (!overlay) return null;
  return {
    highlightEdgeIds: overlay.highlightEdgeIds,
    focusedEdgeIds: overlay.focusedEdgeIds,
    dimUnrelatedEdges: overlay.dimUnrelatedEdges ?? false,
  };
}

/** Captain run id for StepEdge Live animated (pane-level, stable). */
export const GraphCaptainRunIdContext = createContext<string | null>(null);
