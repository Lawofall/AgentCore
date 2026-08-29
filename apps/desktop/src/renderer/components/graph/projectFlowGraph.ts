import {
  challengePreviewFromContext,
  debateFacePrimaryFromContext,
} from "@/components/chat/debate/debateFaceCopy";
import type { InjectGraphOverlay } from "@/lib/causalInject";
import { NODE_HEIGHT, NODE_WIDTH } from "@/lib/elk-layout";
import type { GroupLayout } from "@/lib/elk-layout";
import {
  estimateTokens,
  formatCostCaption,
  headText,
  pickCostMoney,
  tailText,
} from "@/lib/format";
import { detectReviewConcern } from "@/lib/reviewConcern";
import {
  isGraphTraceEnabled,
  traceGraphProjection,
} from "@/services/graphTrace";
import type { Execution, RunNode, RunStatus } from "@/stores/execution";
import { debateBeatFromContext } from "@/stores/execution";
import type { GraphEdge } from "@/stores/graph";
import type { Edge, Node } from "@xyflow/react";
import { type ActCardLayout, actCardDataFromScene } from "./actLod";
import {
  isDebateAgentNode,
  pickEscalationKind,
  revisionFeedbackSummary,
} from "./agentNode/shared";
import { INPUT_ID } from "./constants";
import { agentNodeToShell } from "./graphLive";
import {
  aggregateDebateRoundStatus,
  captainSinkPreview,
  debateRoundActiveBeat,
  debateRoundPhaseLabel,
  debateRoundSettledMark,
  deriveArtifacts,
  isDebateFoldedBeatRun,
  pickDebateCrossExamActivateId,
  resolveHandoff,
  workerRunsOf,
} from "./helpers";
import type { GraphScene } from "./scene";

export interface FlowGraphProjectionInput {
  execution: Execution;
  positions: Record<string, { x: number; y: number }>;
  nodeHeights: Record<string, number>;
  nodeSizes: Record<string, { width: number; height: number }>;
  handleDirection: "horizontal" | "vertical";
  litRunId: string | null;
  litEndpointMessageId: string | null;
  captainRun: { id: string } | null;
  captainStatus: RunStatus | null;
  finalAnswer: { id: string; content: string } | null;
  /** CEO 汇总空窗：无终稿气泡时挂 `team_synthesis_preview` 片段（非 content_delta）。 */
  captainSynthesisPreview?: string;
  /** CEO 协调等待副标题（覆盖 derived status 文案）。 */
  captainStatusCaption?: string | null;
  taskMessage: { id: string } | null;
  activateNode: (id: string) => void;
  groups: GroupLayout[];
  /** Structural IR — the single source for fold / group / beat-fold attribution. */
  scene: GraphScene;
  expandedUnits?: ReadonlySet<string>;
  onToggleUnitExpand?: (unitId: string) => void;
  /** Prefer bezier edges (tree / mind-map look). */
  edgePathType?: "smoothstep" | "bezier";
  /**
   * Document shells only: omit Live fields / callbacks / animated.
   * Faces self-read Live via {@link graphLive} when GraphDocumentMode is on.
   */
  documentShell?: boolean;
}

export interface FlowEdgeProjectionInput extends FlowGraphProjectionInput {
  edges: GraphEdge[];
  injectOverlay?: InjectGraphOverlay | null;
}

/** Cross-axis center for port sorting (y for horizontal flow, x for vertical). */
function crossCenterOf(
  id: string,
  positions: Record<string, { x: number; y: number }>,
  horizontal: boolean,
  heightOf: (id: string) => number,
): number {
  const p = positions[id];
  if (!p) return 0;
  return horizontal ? p.y + heightOf(id) / 2 : p.x + NODE_WIDTH / 2;
}

/** Assign evenly spaced handle ports for fan-out / fan-in edges. */
function computeEdgePorts(
  edges: GraphEdge[],
  positions: Record<string, { x: number; y: number }>,
  horizontal: boolean,
  heightOf: (id: string) => number,
): Map<
  string,
  {
    sourcePortIndex: number;
    sourcePortTotal: number;
    targetPortIndex: number;
    targetPortTotal: number;
  }
> {
  const result = new Map<
    string,
    {
      sourcePortIndex: number;
      sourcePortTotal: number;
      targetPortIndex: number;
      targetPortTotal: number;
    }
  >();

  const bySource = new Map<string, GraphEdge[]>();
  const byTarget = new Map<string, GraphEdge[]>();
  for (const e of edges) {
    const srcArr = bySource.get(e.source);
    if (srcArr) srcArr.push(e);
    else bySource.set(e.source, [e]);
    const tgtArr = byTarget.get(e.target);
    if (tgtArr) tgtArr.push(e);
    else byTarget.set(e.target, [e]);
  }

  const sourcePort = new Map<string, { index: number; total: number }>();
  for (const group of bySource.values()) {
    const sorted = [...group].sort(
      (a, b) =>
        crossCenterOf(a.target, positions, horizontal, heightOf) -
        crossCenterOf(b.target, positions, horizontal, heightOf),
    );
    const total = sorted.length;
    sorted.forEach((e, index) => {
      sourcePort.set(e.id, { index, total });
    });
  }

  const targetPort = new Map<string, { index: number; total: number }>();
  for (const group of byTarget.values()) {
    const sorted = [...group].sort(
      (a, b) =>
        crossCenterOf(a.source, positions, horizontal, heightOf) -
        crossCenterOf(b.source, positions, horizontal, heightOf),
    );
    const total = sorted.length;
    sorted.forEach((e, index) => {
      targetPort.set(e.id, { index, total });
    });
  }

  for (const e of edges) {
    const sp = sourcePort.get(e.id) ?? { index: 0, total: 1 };
    const tp = targetPort.get(e.id) ?? { index: 0, total: 1 };
    result.set(e.id, {
      sourcePortIndex: sp.index,
      sourcePortTotal: sp.total,
      targetPortIndex: tp.index,
      targetPortTotal: tp.total,
    });
  }
  return result;
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

/** Pure projection: Execution + layout → React Flow nodes. */
export function projectFlowNodes({
  execution,
  positions,
  nodeSizes,
  handleDirection,
  litRunId,
  litEndpointMessageId,
  captainRun,
  captainStatus,
  finalAnswer,
  captainSynthesisPreview,
  captainStatusCaption,
  taskMessage,
  activateNode,
  groups,
  scene,
  expandedUnits = new Set(),
  onToggleUnitExpand,
  documentShell = false,
}: FlowGraphProjectionInput): Node[] {
  const placed = (id: string) => positions[id];

  const workerRuns = workerRunsOf(execution.runs);
  const workerIdSet = new Set(workerRuns.map((r) => r.id));
  // Structural conclusions come from the scene — never re-derived here.
  const foldInfo = scene.fold;
  const subTeams = scene.subTeams;
  const runById = new Map(execution.runs.map((r) => [r.id, r]));
  const nodes: Node[] = [];
  const missingPosIds: string[] = [];
  const foldedIds: string[] = [];

  const rootGroupIds = new Set(
    groups
      .filter((g) => {
        const st = subTeams.find((s) => s.groupId === g.groupId);
        return st && !subTeams.some((o) => o.memberIds.includes(st.parentId));
      })
      .map((g) => g.groupId),
  );
  const orderedGroups = [
    ...groups.filter((g) => rootGroupIds.has(g.groupId)),
    ...groups.filter((g) => !rootGroupIds.has(g.groupId)),
  ];

  for (const group of orderedGroups) {
    const st = subTeams.find((s) => s.groupId === group.groupId);
    if (!st) continue;
    const parentRun = execution.runs.find((r) => r.id === st.parentId);
    const parentAgent = parentRun
      ? execution.agents.find((a) => a.id === parentRun.agentId)
      : null;
    const outerSt = subTeams.find((s) => s.memberIds.includes(st.parentId));
    const outerGroup = outerSt
      ? groups.find((g) => g.groupId === outerSt.groupId)
      : undefined;
    const absPos = { x: group.x, y: group.y };
    const pos = outerGroup
      ? { x: absPos.x - outerGroup.x, y: absPos.y - outerGroup.y }
      : absPos;
    nodes.push({
      id: group.groupId,
      type: "subTeamGroup",
      position: pos,
      style: { width: group.width, height: group.height },
      ...(outerGroup
        ? { parentId: outerGroup.groupId, extent: "parent" as const }
        : {}),
      data: {
        parentRole: parentAgent?.role ?? st.parentId,
        memberCount: st.memberIds.length + 1,
        handleDirection,
        ...(foldInfo.debateUnits.has(st.parentId)
          ? { variant: "debate" as const }
          : {}),
      },
      zIndex: -1,
    } as Node);
  }

  for (const [i, run] of workerRuns.entries()) {
    // 质询/复攻/crux 折进同轮宿主，不独立成图节点。
    if (isDebateFoldedBeatRun(run)) {
      foldedIds.push(run.id);
      continue;
    }

    const unit = foldInfo.unitOf.get(run.id) ?? run.id;
    const isFoldedChild = foldInfo.folded.has(run.id);
    const unitExpanded =
      foldInfo.debateUnits.has(unit) || expandedUnits.has(unit);
    if (isFoldedChild && !unitExpanded) {
      foldedIds.push(run.id);
      continue;
    }

    // Which compound this run renders in — the scene's single「接续链归盒」
    // conclusion (revisions already resolved to their box). Never re-walked here.
    const groupId = scene.nodeGroup.get(run.id) ?? null;
    const group = groupId
      ? groups.find((g) => g.groupId === groupId)
      : undefined;

    let pos: { x: number; y: number } | undefined;
    if (group) {
      const absPos = positions[run.id];
      if (!absPos) {
        missingPosIds.push(run.id);
        continue;
      }
      pos = {
        x: absPos.x - group.x,
        y: absPos.y - group.y,
      };
    } else {
      pos = placed(run.id);
    }
    if (!pos) {
      missingPosIds.push(run.id);
      continue;
    }

    // Debate beats (质询/复攻/crux) folded into this round host — the scene's
    // single conclusion; we only resolve ids → run data here.
    const foldedCx = (scene.beatFoldsByHost.get(run.id) ?? [])
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
    const output = agent ? agent.outputChunks.join("") : "";
    const reasoning = agent ? agent.reasoningChunks.join("") : "";
    const reviewConcern =
      output.length >= 12
        ? detectReviewConcern(output, {
            role: agent?.role ?? faceRun.role,
            runId: faceRun.id,
          })
        : null;
    const focused =
      litRunId === run.id || foldedCx.some((r) => r.id === litRunId);
    const isContinuation = run.continuesRunId != null;
    const isSubtask =
      !isContinuation &&
      !!run.parentRunId &&
      run.parentRunId !== run.id &&
      workerIdSet.has(run.parentRunId);
    const foldedChildCount = foldInfo.descendants.get(run.id)?.length ?? 0;
    const size = nodeSizes[run.id];
    const durationMs =
      foldedCx.length > 0 ? sumDurationMs(roundRuns) : run.durationMs;
    // 轮节点聚合口径与 durationMs 一致：成本 / token 计入折进的质询作答。
    // BYOK：记账 total=0 时用 estimated_total（美元社区价目），节点上标「自带密钥·估算」。
    // 无 FX：折进的 run 同凭据来源 → 同币种，按首个记名。
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
    const agentData = {
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
      outputPreview: tailText(output),
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
      reasoningPreview: tailText(reasoning),
      toolProgress: agent?.toolProgress ?? null,
      toolExecutionLive: agent?.toolExecutionLive ?? null,
      phase: faceRun.phase ?? run.phase ?? null,
      phaseTool: faceRun.phaseTool ?? run.phaseTool ?? null,
      tokenCount: estimateTokens(output),
      toolCount: agent?.toolCalls.length ?? 0,
      artifacts: agent ? deriveArtifacts(agent.toolCalls) : [],
      focused,
      nodeWidth: size?.width,
      model: faceRun.model ?? run.model,
      durationMs,
      // 进行中 live 计时锚点：取当前在跑的 beat（辩论折叠轮取 faceRun），回退轮根。
      startedAt: faceRun.startedAt ?? run.startedAt,
      realTokens,
      costText:
        costNano > 0
          ? formatCostCaption(costNano, costEstimated, costCurrency)
          : undefined,
      handleDirection,
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
        settledMark && cxActivateId
          ? () => activateNode(cxActivateId)
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
        (n, r) =>
          n + r.escalations.filter((e) => e.status === "pending").length,
        0,
      ),
      escalationRaised: roundRuns.reduce(
        (n, r) => n + r.escalations.filter((e) => e.status === "raised").length,
        0,
      ),
      escalationKind: pickEscalationKind(
        roundRuns.flatMap((r) => r.escalations),
      ),
      reviewConcern,
      foldedChildCount:
        foldedChildCount > 0 && !foldInfo.debateUnits.has(run.id)
          ? foldedChildCount
          : undefined,
      unitExpanded: expandedUnits.has(run.id),
      onToggleUnitExpand: () => onToggleUnitExpand?.(run.id),
      enterIndex: i + 1,
      onActivate: () => activateNode(activateId),
    };
    nodes.push({
      id: run.id,
      type: "agent",
      position: pos,
      ...(group ? { parentId: group.groupId, extent: "parent" as const } : {}),
      data: documentShell ? agentNodeToShell(agentData) : agentData,
    } as Node);
  }

  if (execution.runs.length > 0) {
    const inputPos = placed(INPUT_ID);
    if (inputPos) {
      nodes.push({
        id: INPUT_ID,
        type: "userInput",
        position: inputPos,
        data: documentShell
          ? {
              variant: "input" as const,
              label: execution.taskSummary,
              handleDirection,
              enterIndex: 0,
            }
          : {
              variant: "input" as const,
              status: "completed" as const,
              label: execution.taskSummary,
              handleDirection,
              enterIndex: 0,
              focused: !!taskMessage && litEndpointMessageId === taskMessage.id,
              onActivate: taskMessage
                ? () => activateNode(INPUT_ID)
                : undefined,
            },
      } as Node);
    }
    // Document shell: emit captain whenever the run exists (Live fills status).
    // Legacy path still requires captainStatus (callers always derive it).
    if (captainRun && (documentShell || captainStatus)) {
      const captainPos = placed(captainRun.id);
      if (captainPos) {
        const waitCaption = (captainStatusCaption ?? "").trim();
        // Coordination wait uses running chrome (spinner) even while derived
        // captain status is still pending (workers in flight).
        const sinkStatus: RunStatus = waitCaption
          ? "running"
          : (captainStatus ?? "pending");
        const preview = captainSinkPreview({
          captainStatus: captainStatus ?? "pending",
          answerPreview: finalAnswer ? headText(finalAnswer.content) : "",
          synthesisPreview: captainSynthesisPreview,
          waitCaption,
        });
        nodes.push({
          id: captainRun.id,
          type: "captain",
          position: captainPos,
          data: documentShell
            ? {
                variant: "captain" as const,
                runId: captainRun.id,
                label: "",
                handleDirection,
                enterIndex: workerRuns.length + 1,
              }
            : {
                variant: "captain" as const,
                status: sinkStatus,
                statusCaption: waitCaption || undefined,
                label: "",
                preview,
                handleDirection,
                enterIndex: workerRuns.length + 1,
                focused:
                  !!finalAnswer && litEndpointMessageId === finalAnswer.id,
                onActivate: finalAnswer
                  ? () => activateNode(captainRun.id)
                  : undefined,
              },
        } as Node);
      } else {
        missingPosIds.push(captainRun.id);
      }
    }
  }

  if (isGraphTraceEnabled()) {
    // 首帧 ELK 未就绪时 positions 空是预期，不记成缺节点。
    if (Object.keys(positions).length === 0) {
      return nodes;
    }
    const agentNodeIds = nodes
      .filter((n) => n.type === "agent" || n.type === "captain")
      .map((n) => n.id);
    const runIds = execution.runs.map((r) => r.id);
    const expectedVisible = runIds.filter((id) => !foldedIds.includes(id));
    const gap =
      missingPosIds.length > 0 ||
      expectedVisible.some((id) => !agentNodeIds.includes(id));
    if (gap) {
      traceGraphProjection({
        runIds,
        agentNodeIds,
        missingPosIds: [...missingPosIds],
        foldedIds: [...foldedIds],
        posKeyCount: Object.keys(positions).length,
        layoutReady: true,
      });
    }
  }

  return nodes;
}

/**
 * Project folded-act cards (幕级 LOD, 批 R2). One `actSummary` node per non-focused
 * act, data pulled straight off the derived {@link GraphScene.acts} — clicking one
 * focuses that act (`onFocusAct`).
 */
export function projectActCardNodes(
  scene: GraphScene,
  cards: ActCardLayout[],
  handleDirection: "horizontal" | "vertical",
  onFocusAct: (actId: string) => void,
  documentShell = false,
): Node[] {
  const actById = new Map(scene.acts.map((a) => [a.actId, a]));
  const nodes: Node[] = [];
  for (const c of cards) {
    const act = actById.get(c.actId);
    if (!act) continue;
    const full = {
      ...actCardDataFromScene(act, c.index),
      handleDirection,
      onActivate: () => onFocusAct(c.actId),
    };
    nodes.push({
      id: c.id,
      type: "actSummary",
      position: { x: c.x, y: c.y },
      draggable: false,
      data: documentShell
        ? {
            actId: full.actId,
            kind: full.kind,
            title: full.title,
            authorizedBy: full.authorizedBy,
            roles: full.roles,
            handleDirection,
            index: full.index,
          }
        : full,
    } as Node);
  }
  return nodes;
}

/** Pure projection: layout edges + execution status → React Flow edges. */
export function projectFlowEdges({
  edges,
  injectOverlay,
  execution,
  positions,
  nodeSizes,
  handleDirection,
  captainRun,
  captainStatus,
  edgePathType = "smoothstep",
  documentShell = false,
}: Pick<
  FlowEdgeProjectionInput,
  | "edges"
  | "injectOverlay"
  | "execution"
  | "positions"
  | "nodeSizes"
  | "handleDirection"
  | "captainRun"
  | "captainStatus"
  | "edgePathType"
  | "documentShell"
>): Edge[] {
  // Document: topology only — inject gap edges are a Live overlay (GraphView).
  const gapEdges =
    documentShell || !injectOverlay ? [] : (injectOverlay.activeGapEdges ?? []);
  const allEdges = gapEdges.length > 0 ? [...edges, ...gapEdges] : edges;
  const horizontal = handleDirection === "horizontal";
  const heightOf = (id: string) => nodeSizes[id]?.height ?? NODE_HEIGHT;
  const ports = computeEdgePorts(allEdges, positions, horizontal, heightOf);

  return allEdges.map((e) => {
    const kind = e.kind ?? "dep";
    const handoff =
      kind === "dep" ? resolveHandoff(execution, e.source, e.target) : null;
    const port = ports.get(e.id);
    if (documentShell) {
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: "step",
        data: {
          kind,
          handoff,
          handleDirection,
          pathType: edgePathType,
          sourcePortIndex: port?.sourcePortIndex ?? 0,
          sourcePortTotal: port?.sourcePortTotal ?? 1,
          targetPortIndex: port?.targetPortIndex ?? 0,
          targetPortTotal: port?.targetPortTotal ?? 1,
        },
      } as Edge;
    }
    const animated =
      e.target === captainRun?.id
        ? captainStatus === "running"
        : execution.runs.find((s) => s.id === e.target)?.status === "running";
    const injectHighlight =
      kind === "inject" || (injectOverlay?.highlightEdgeIds.has(e.id) ?? false);
    const injectDimmed =
      (injectOverlay?.dimUnrelatedEdges ?? false) &&
      !(injectOverlay?.focusedEdgeIds.has(e.id) ?? false);
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      type: "step",
      animated,
      data: {
        animated,
        kind,
        handoff,
        injectHighlight,
        injectDimmed,
        handleDirection,
        pathType: edgePathType,
        sourcePortIndex: port?.sourcePortIndex ?? 0,
        sourcePortTotal: port?.sourcePortTotal ?? 1,
        targetPortIndex: port?.targetPortIndex ?? 0,
        targetPortTotal: port?.targetPortTotal ?? 1,
      },
    } as Edge;
  });
}

/** Live-only inject gap edges (Document topology stays untouched). */
export function projectInjectGapEdges({
  injectOverlay,
  positions,
  nodeSizes,
  handleDirection,
  edgePathType = "smoothstep",
}: {
  injectOverlay:
    | import("@/lib/causalInject").InjectGraphOverlay
    | null
    | undefined;
  positions: Record<string, { x: number; y: number }>;
  nodeSizes: Record<string, { width: number; height: number }>;
  handleDirection: "horizontal" | "vertical";
  edgePathType?: "smoothstep" | "bezier";
}): Edge[] {
  const gapEdges = injectOverlay?.activeGapEdges ?? [];
  if (gapEdges.length === 0) return [];
  const horizontal = handleDirection === "horizontal";
  const heightOf = (id: string) => nodeSizes[id]?.height ?? NODE_HEIGHT;
  const ports = computeEdgePorts(gapEdges, positions, horizontal, heightOf);
  return gapEdges.map((e) => {
    const port = ports.get(e.id);
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      type: "step",
      data: {
        kind: "inject" as const,
        handleDirection,
        pathType: edgePathType,
        sourcePortIndex: port?.sourcePortIndex ?? 0,
        sourcePortTotal: port?.sourcePortTotal ?? 1,
        targetPortIndex: port?.targetPortIndex ?? 0,
        targetPortTotal: port?.targetPortTotal ?? 1,
      },
    } as Edge;
  });
}
