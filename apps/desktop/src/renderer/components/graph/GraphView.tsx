import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu";
import { useTurnAudit } from "@/hooks/useTurnAudit";
import { resolveEffectiveGraphLayout } from "@/lib/graph-layout-utils";
import "@/services/graphStress";
import {
  isTerminalPhase,
  useActiveTurnPhase,
  useConversationStore,
} from "@/stores/conversation";
import { useDisclosureStore } from "@/stores/disclosure";
import {
  projectRuntime,
  useActiveExecField,
  useExecutionScope,
  useExecutionStore,
} from "@/stores/execution";
import { useGraphStore } from "@/stores/graph";
import type { EndpointKind } from "@/stores/sidePanel";
import { Background, type Edge, type Node, ReactFlow } from "@xyflow/react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CanvasZoomControls } from "./CanvasZoomControls";
import { DebateStageBands } from "./DebateStageBands";
import { GraphActionBar } from "./GraphActionBar";
import { GraphContextMenu } from "./GraphContextMenu";
import { GraphLayoutError } from "./GraphLayoutError";
import { GraphTeamStopControl } from "./GraphTeamStopControl";
import { GraphToolbar } from "./GraphToolbar";
import { WaveLanes } from "./WaveLanes";
import { edgeTypes, nodeTypes } from "./constants";
import type { GraphActionsValue } from "./graphActions";
import { GraphActionsContext } from "./graphActions";
import {
  graphDocumentFingerprint,
  graphShellSnapshotKey,
  graphViewExecutionEpoch,
} from "./graphDocument";
import { GraphFlowHost } from "./graphHost";
import { GraphHoverContext, useGraphHoverState } from "./graphHover";
import {
  GraphCaptainRunIdContext,
  GraphDocumentModeContext,
  GraphInjectPaintContext,
  GraphSceneContext,
  injectPaintFromOverlay,
} from "./graphLive";
import {
  GRAPH_UNIT_EXPAND_TOUCHED,
  resolveGraphExpandedUnits,
} from "./graphUnitExpand";
import type { GraphPendingDecision } from "./pendingDecisions";
import { executionGraphCapabilities } from "./planCapabilities";
import { projectInjectGapEdges } from "./projectFlowGraph";
import { projectTurnGraph } from "./projectTurnGraph";
import { buildGraphScene } from "./scene";
import { useActFocus } from "./useActFocus";
import { useGraphDrillIn } from "./useGraphDrillIn";
import { useGraphInjectFlow } from "./useGraphInjectFlow";
import { expandedUnitsFromFold, useGraphLayout } from "./useGraphLayout";
import { useGraphPendingDecisions } from "./useGraphPendingDecisions";
import { type GraphFitMode, useGraphViewport } from "./useGraphViewport";
import { positionsMorphSig, useLayoutMorph } from "./useLayoutMorph";

export type { GraphHoverState } from "./graphHover";
export { GraphHoverContext };

interface GraphViewProps {
  interactive?: boolean;
  fitMode?: GraphFitMode;
  onNodeSelect?: (runId: string) => void;
  onEndpointSelect?: (
    contentMessageId: string,
    title: string,
    endpoint: EndpointKind,
  ) => void;
  onMeasure?: (m: { height: number; overflowing: boolean }) => void;
  onClose?: () => void;
}

export const GraphView = memo(function GraphView({
  interactive = true,
  fitMode = "view",
  onNodeSelect,
  onEndpointSelect,
  onMeasure,
  onClose,
}: GraphViewProps = {}) {
  const messageId = useExecutionScope();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const turnPhase = useActiveTurnPhase();
  const turnTerminal = isTerminalPhase(turnPhase);
  // Parent Document tree: subscribe to structure+lifecycle epoch only.
  // Streaming rt identity changes must not commit GraphView / RF shells;
  // Live faces use per-run signature hooks in graphLive.
  const structureStatusEpoch = useActiveExecField((rt) =>
    graphViewExecutionEpoch(projectRuntime(rt)),
  );
  const execution = useMemo(() => {
    if (!structureStatusEpoch || !messageId) return null;
    const rt = useExecutionStore.getState().byId[messageId];
    return rt ? projectRuntime(rt) : null;
  }, [structureStatusEpoch, messageId]);
  const caps = executionGraphCapabilities(execution);
  const { data: turnAudit } = useTurnAudit(
    caps.auditInject ? conversationId : null,
    caps.auditInject ? messageId : null,
  );
  const showAuditInjectFlow = useGraphStore((s) => s.showAuditInjectFlow);
  const setShowAuditInjectFlow = useGraphStore((s) => s.setShowAuditInjectFlow);
  const layoutKind = useGraphStore((s) => s.layoutKind);
  const setLayoutKind = useGraphStore((s) => s.setLayoutKind);
  const effectiveLayoutKind = resolveEffectiveGraphLayout(layoutKind);
  // 内嵌模式：expandedUnits 按对话持久化（与画布 graph-fold 独立）。
  // 默认展开有子队的 unit（对齐画布 / 协作图 UX「默认展开」）；用户点过收起后才记覆盖。
  // 交互/全屏 GraphView 仍用会话内存态；画布多回合折叠走 graph store。
  const persistEmbedUnits = !interactive && !!messageId && !!conversationId;
  const embedUnitPrefix = persistEmbedUnits
    ? `${conversationId}::${messageId}:embed-unit:`
    : null;
  const setDisclosureKey = useDisclosureStore((s) => s.setKey);
  const embedTouchedKey = embedUnitPrefix
    ? `${embedUnitPrefix}${GRAPH_UNIT_EXPAND_TOUCHED}`
    : null;
  const embedTouched = useDisclosureStore((s) =>
    embedTouchedKey ? !!s.map[embedTouchedKey] : false,
  );
  const embedUnitsFingerprint = useDisclosureStore((s) => {
    if (!embedUnitPrefix) return "";
    const ids: string[] = [];
    for (const [k, v] of Object.entries(s.map)) {
      if (!v || !k.startsWith(embedUnitPrefix)) continue;
      const id = k.slice(embedUnitPrefix.length);
      if (id === GRAPH_UNIT_EXPAND_TOUCHED) continue;
      ids.push(id);
    }
    return ids.sort().join(",");
  });
  // biome-ignore lint/correctness/useExhaustiveDependencies: epoch gates execution identity
  const defaultExpandedUnits = useMemo(() => {
    if (!execution) return new Set<string>();
    return expandedUnitsFromFold(execution.runs, new Set());
  }, [structureStatusEpoch]);
  const [sessionExpandedUnits, setSessionExpandedUnits] =
    useState<Set<string> | null>(null);
  const expandedUnits = useMemo(
    () =>
      resolveGraphExpandedUnits({
        defaults: defaultExpandedUnits,
        touched: embedTouched,
        storedFingerprint: embedUnitsFingerprint,
        sessionOverride: sessionExpandedUnits,
        persist: !!embedUnitPrefix,
      }),
    [
      defaultExpandedUnits,
      embedTouched,
      embedUnitsFingerprint,
      sessionExpandedUnits,
      embedUnitPrefix,
    ],
  );
  const onToggleUnitExpand = useCallback(
    (unitId: string) => {
      if (!embedUnitPrefix) {
        setSessionExpandedUnits((prev) => {
          const next = new Set(prev ?? defaultExpandedUnits);
          if (next.has(unitId)) next.delete(unitId);
          else next.add(unitId);
          return next;
        });
        return;
      }
      const current = resolveGraphExpandedUnits({
        defaults: defaultExpandedUnits,
        touched: embedTouched,
        storedFingerprint: embedUnitsFingerprint,
        sessionOverride: null,
        persist: true,
      });
      if (current.has(unitId)) current.delete(unitId);
      else current.add(unitId);
      if (embedTouchedKey) setDisclosureKey(embedTouchedKey, true, false);
      const known = new Set([...defaultExpandedUnits, ...current, unitId]);
      for (const id of known) {
        setDisclosureKey(`${embedUnitPrefix}${id}`, current.has(id), false);
      }
    },
    [
      embedUnitPrefix,
      embedTouchedKey,
      embedTouched,
      embedUnitsFingerprint,
      defaultExpandedUnits,
      setDisclosureKey,
    ],
  );
  // 幕级 LOD（批 R2）：多幕回合聚焦态（进行中自动聚焦活跃幕，完成态整链折叠为卡）。
  // 单幕回合 focusScene.acts.length < 2，focusedActId 恒被 useGraphLayout 忽略。
  // 跟 structure/lifecycle epoch 门控，勿绑每帧变的 execution 引用。
  // biome-ignore lint/correctness/useExhaustiveDependencies: epoch + expand gate scene
  const focusScene = useMemo(
    () => (execution ? buildGraphScene(execution, { expandedUnits }) : null),
    [structureStatusEpoch, expandedUnits],
  );
  const { focusedActId, focusAct, collapseActs } = useActFocus(
    focusScene,
    execution?.status,
  );
  const isMultiAct = (focusScene?.acts.length ?? 0) >= 2;
  const {
    positions,
    edges,
    bbox,
    layoutReady,
    layoutError,
    nodeSizes,
    onNodesChange,
    groups,
    scene,
    actCards,
  } = useGraphLayout(
    execution,
    effectiveLayoutKind,
    fitMode,
    expandedUnits,
    focusedActId,
  );
  const { containerRef, rfRef, overflowing, fitView, centerNode, onInit } =
    useGraphViewport({
      fitMode,
      bbox,
      layoutReady,
      onMeasure,
    });
  const handleDirection =
    effectiveLayoutKind === "leftright"
      ? ("horizontal" as const)
      : ("vertical" as const);

  const {
    activateNode,
    showRunDetailHere,
    litRunId,
    litEndpointMessageId,
    finalAnswerId,
    taskMessageId,
    captainRun,
  } = useGraphDrillIn(execution, {
    interactive,
    messageId,
    onNodeSelect,
    onEndpointSelect,
    onClose,
  });

  // Audit inject flow (shared hook) over this turn's structural edges.
  const { injectOverlay, injectFlowAvailable } = useGraphInjectFlow({
    enabled: caps.auditInject,
    causalGraph: turnAudit?.causal_graph,
    edges,
    litRunId,
    showAllInject: showAuditInjectFlow,
  });

  // Hover (shared hook) — single-turn ids, no canvas namespacing.
  const injectRelatedIds = injectOverlay?.dimUnrelatedEdges
    ? injectOverlay.relatedNodeIds
    : null;
  const { setHoveredNodeId, hoverState } = useGraphHoverState({
    edges,
    injectRelatedIds,
  });

  const [menuNodeId, setMenuNodeId] = useState<string | null>(null);

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      // 幕摘要卡：点击聚焦该幕（展开为唯一聚焦幕），不走节点钻取。
      if (node.type === "actSummary") {
        const actId = (node.data as { actId?: string })?.actId;
        if (actId) focusAct(actId);
        return;
      }
      activateNode(node.id);
    },
    [activateNode, focusAct],
  );

  const onPaneClick = useCallback(() => {
    // 多幕：点空白折回整链幕摘要卡（R3 图头行动条前的轻量「缩回」手势）。
    if (isMultiAct && focusedActId) collapseActs();
  }, [isMultiAct, focusedActId, collapseActs]);

  // 图头行动条（R3）：聚合本回合待拍板；点击定位到对应节点。
  const pendingDecisions = useGraphPendingDecisions(conversationId, messageId);
  const [locateTarget, setLocateTarget] = useState<{
    runId: string;
    actId: string | null;
  } | null>(null);
  const onLocateDecision = useCallback(
    (d: GraphPendingDecision) => {
      if (!d.runId) {
        // execution 级且无代表节点（罕见）：退回整图 fit。
        fitView();
        return;
      }
      // 折叠幕内：先聚焦目标幕，聚焦幕 relayout 后再居中（下方 effect 承接）。
      if (isMultiAct && d.actId && d.actId !== focusedActId) focusAct(d.actId);
      activateNode(d.runId);
      // fit-width 内联态整图已尽收，居中无意义 → 仅高亮 + 开详情。
      if (fitMode !== "width")
        setLocateTarget({ runId: d.runId, actId: d.actId });
    },
    [isMultiAct, focusedActId, focusAct, activateNode, fitView, fitMode],
  );
  // 聚焦幕重排是异步的——等目标进 positions 再居中一次。
  useEffect(() => {
    if (!locateTarget || !layoutReady) return;
    if (!positions[locateTarget.runId]) return;
    const raf = requestAnimationFrame(() => centerNode(locateTarget.runId));
    setLocateTarget(null);
    return () => cancelAnimationFrame(raf);
  }, [locateTarget, layoutReady, positions, centerNode]);
  // 兜底：目标始终不定位（如折进子队的 run）时清除，避免悬挂。
  useEffect(() => {
    if (!locateTarget) return;
    const t = window.setTimeout(() => setLocateTarget(null), 2500);
    return () => window.clearTimeout(t);
  }, [locateTarget]);

  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node) => {
      event.preventDefault();
      setMenuNodeId(node.id);
    },
    [],
  );

  const hoverClearRef = useRef<number | null>(null);

  const onNodeMouseEnter = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (hoverClearRef.current != null) {
        cancelAnimationFrame(hoverClearRef.current);
        hoverClearRef.current = null;
      }
      setHoveredNodeId(node.id);
    },
    [setHoveredNodeId],
  );

  const onNodeMouseLeave = useCallback(() => {
    hoverClearRef.current = requestAnimationFrame(() => {
      setHoveredNodeId(null);
      hoverClearRef.current = null;
    });
  }, [setHoveredNodeId]);

  const onPaneContextMenu = useCallback(
    (event: React.MouseEvent | MouseEvent) => {
      event.preventDefault();
      setMenuNodeId(null);
    },
    [],
  );

  const documentFingerprint = useMemo(() => {
    if (!execution) return "";
    return graphDocumentFingerprint({
      execution,
      expandedUnits,
      focusedActId,
      handleDirection,
    });
  }, [execution, expandedUnits, focusedActId, handleDirection]);

  const shellSnapshotKey = useMemo(() => {
    if (!layoutReady) return "";
    return graphShellSnapshotKey({
      positions,
      groups,
      nodeSizes,
      actCards,
      bbox,
      edgeIds: edges.map((e) => e.id),
    });
  }, [layoutReady, positions, groups, nodeSizes, actCards, bbox, edges]);

  // Document projection: only re-run when structure fingerprint or shell
  // positions change — never on streaming deltas. Live fields self-subscribe.
  const executionRef = useRef(execution);
  executionRef.current = execution;
  const sceneRef = useRef(scene);
  sceneRef.current = scene;
  // Document gate: fingerprint + shell snapshot — not execution identity / live fields.
  // biome-ignore lint/correctness/useExhaustiveDependencies: gated by Document keys
  const projected = useMemo(() => {
    const ex = executionRef.current;
    const sc = sceneRef.current;
    if (!ex || !sc || !documentFingerprint) return null;
    return projectTurnGraph({
      execution: ex,
      scene: sc,
      positions,
      nodeHeights: {},
      nodeSizes,
      groups,
      bbox,
      actCards,
      edges,
      handleDirection,
      litRunId: null,
      litEndpointMessageId: null,
      captainRun,
      captainStatus: null,
      finalAnswer: null,
      captainSynthesisPreview: "",
      captainStatusCaption: null,
      taskMessage: null,
      activateNode: () => undefined,
      expandedUnits,
      onToggleUnitExpand: undefined,
      injectOverlay: null,
      layoutKind: effectiveLayoutKind,
      onFocusAct: () => undefined,
      documentShell: true,
    });
  }, [
    documentFingerprint,
    shellSnapshotKey,
    positions,
    nodeSizes,
    groups,
    bbox,
    actCards,
    edges,
    handleDirection,
    captainRun,
    expandedUnits,
    effectiveLayoutKind,
  ]);

  const graphActions = useMemo<GraphActionsValue>(
    () => ({
      activateNode,
      toggleUnitExpand: onToggleUnitExpand,
      focusAct,
      litRunId,
      litEndpointMessageId,
      taskMessageId,
      finalAnswerId,
      turnTerminal,
    }),
    [
      activateNode,
      onToggleUnitExpand,
      focusAct,
      litRunId,
      litEndpointMessageId,
      taskMessageId,
      finalAnswerId,
      turnTerminal,
    ],
  );

  const injectPaint = useMemo(
    () => injectPaintFromOverlay(injectOverlay),
    [injectOverlay],
  );

  const injectGapEdges = useMemo(
    () =>
      projectInjectGapEdges({
        injectOverlay,
        positions,
        nodeSizes,
        handleDirection,
      }),
    [injectOverlay, positions, nodeSizes, handleDirection],
  );

  // 结构换坐标时短暂 morph（与画布一致）；流式内容更新不改 positions → 不触发。
  const layoutSig = useMemo(
    () => (!layoutReady || !bbox ? "" : positionsMorphSig(positions)),
    [layoutReady, bbox, positions],
  );
  const morphing = useLayoutMorph(layoutSig);

  const flowNodes = useMemo(() => {
    const nodes = projected?.nodes ?? [];
    if (!morphing) return nodes;
    return nodes.map((n) => ({
      ...n,
      className: [n.className, "graph-layout-morphing"]
        .filter(Boolean)
        .join(" "),
    }));
  }, [projected, morphing]);

  const docEdges = projected?.edges ?? [];
  const flowEdges = useMemo<Edge[]>(() => {
    if (injectGapEdges.length === 0) return docEdges;
    return [...docEdges, ...injectGapEdges];
  }, [docEdges, injectGapEdges]);
  const laneBands = projected?.lanes ?? [];
  const debateBands = projected?.debateStages ?? [];

  const interactionProps = !interactive
    ? {
        zoomOnScroll: false,
        zoomOnPinch: false,
        zoomOnDoubleClick: false,
        panOnDrag: false,
        preventScrolling: false,
        minZoom: 0.05,
      }
    : {};

  if (!execution) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <div className="text-center">
          <p className="text-sm text-muted-foreground">暂无执行任务</p>
          <p className="mt-1 text-xs text-muted-foreground">
            发送多 Agent 任务后，协作图将在此显示
          </p>
        </div>
      </div>
    );
  }

  return (
    <GraphFlowHost>
      <div className="flex h-full w-full flex-col">
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div ref={containerRef} className="relative min-h-0 flex-1">
              {layoutError ? (
                <GraphLayoutError detail={layoutError} />
              ) : (
                <GraphDocumentModeContext.Provider value={true}>
                  <GraphActionsContext.Provider value={graphActions}>
                    <GraphSceneContext.Provider value={scene}>
                      <GraphCaptainRunIdContext.Provider
                        value={captainRun?.id ?? null}
                      >
                        <GraphInjectPaintContext.Provider value={injectPaint}>
                          <GraphHoverContext.Provider value={hoverState}>
                            <ReactFlow
                              nodes={flowNodes}
                              edges={flowEdges}
                              nodeTypes={nodeTypes}
                              edgeTypes={edgeTypes}
                              onInit={onInit}
                              onNodesChange={onNodesChange}
                              onNodeClick={onNodeClick}
                              onNodeMouseEnter={onNodeMouseEnter}
                              onNodeMouseLeave={onNodeMouseLeave}
                              onNodeContextMenu={onNodeContextMenu}
                              onPaneContextMenu={onPaneContextMenu}
                              onPaneClick={onPaneClick}
                              fitView={fitMode === "view"}
                              nodesDraggable={false}
                              nodesConnectable={false}
                              nodesFocusable={false}
                              elementsSelectable={false}
                              proOptions={{ hideAttribution: true }}
                              {...interactionProps}
                            >
                              <Background gap={20} size={1} />
                              <WaveLanes waves={laneBands} />
                              <DebateStageBands bands={debateBands} />
                            </ReactFlow>
                            {!layoutReady && (
                              <div
                                className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/50"
                                aria-hidden
                              >
                                <div className="h-2 w-24 animate-pulse rounded-full bg-muted" />
                              </div>
                            )}
                          </GraphHoverContext.Provider>
                        </GraphInjectPaintContext.Provider>
                      </GraphCaptainRunIdContext.Provider>
                    </GraphSceneContext.Provider>
                  </GraphActionsContext.Provider>
                </GraphDocumentModeContext.Provider>
              )}

              {fitMode === "width" && overflowing && (
                <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-card to-transparent" />
              )}

              {interactive && (
                <GraphToolbar
                  layoutKind={layoutKind}
                  onLayoutKindChange={setLayoutKind}
                  injectFlowAvailable={injectFlowAvailable}
                  showAuditInjectFlow={showAuditInjectFlow}
                  onShowAuditInjectFlowChange={setShowAuditInjectFlow}
                  leading={<GraphTeamStopControl />}
                />
              )}

              {/* 图头行动条：内嵌 / 全屏两宿主同挂，非交互内联态也显（导航不依赖 pan/zoom）。 */}
              {layoutReady && !layoutError && (
                <GraphActionBar
                  decisions={pendingDecisions}
                  onLocate={onLocateDecision}
                />
              )}

              {/* 内联图无 GraphToolbar：任务级停止单独挂右上。 */}
              {!interactive && layoutReady && !layoutError && (
                <GraphTeamStopControl className="absolute right-3 top-3 z-10" />
              )}

              {interactive &&
                injectOverlay &&
                (showAuditInjectFlow || injectOverlay.gapEdges.length > 0) && (
                  <div className="pointer-events-none absolute bottom-3 right-3 z-10 rounded-lg border border-border bg-card/90 px-2.5 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur">
                    <span className="text-foreground">──</span> 计划依赖
                    <span className="mx-2 text-muted-foreground/50">·</span>
                    <span className="text-primary">⇢</span> 数据注入（审计）
                  </div>
                )}

              {interactive && (
                <div className="absolute bottom-3 left-3 z-10">
                  <CanvasZoomControls
                    onZoomIn={() => rfRef.current?.zoomIn({ duration: 200 })}
                    onZoomOut={() => rfRef.current?.zoomOut({ duration: 200 })}
                    onFit={fitView}
                    fitLabel="适应画布 (F)"
                  />
                </div>
              )}
            </div>
          </ContextMenuTrigger>

          <GraphContextMenu
            menuNodeId={menuNodeId}
            captainRunId={captainRun?.id}
            taskMessageId={taskMessageId}
            finalAnswerId={finalAnswerId}
            onNodeSelect={onNodeSelect}
            showRunDetailHere={showRunDetailHere}
            onClose={onClose}
            activateNode={activateNode}
            centerNode={centerNode}
            fitView={fitView}
          />
        </ContextMenu>
      </div>
    </GraphFlowHost>
  );
});
