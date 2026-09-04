import { WaveLanes } from "@/components/graph/WaveLanes";
import { RF_PRO_OPTIONS } from "@/components/graph/constants";
import { type WaveBand, computeWaves } from "@/components/graph/scene";
import {
  EMBED_MIN_HEIGHT,
  type LayoutResult,
  computeLayout,
  fitWidthBox,
} from "@/lib/elk-layout";
import type { ElkGraphLayout } from "@/lib/graph-layout-utils";
import type { Execution, RunStatus } from "@/stores/execution";
import type { GraphEdge } from "@/stores/graph";
import {
  Background,
  type Edge,
  type Node,
  ReactFlow,
  type ReactFlowInstance,
} from "@xyflow/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { PreviewNode, Scenario } from "./scenarioData";
import { edgeTypes, nodeTypes } from "./shared";

/**
 * 共享内嵌画布：跑真实 ELK 布局 + 真实 `WaveLanes` 波次泳道 + 内嵌 fit-to-width 定高，
 * 复刻聊天内嵌协作图。节点形态（`nodes`/`edges`）静态，运行状态由 `statuses` 外注 ——
 * 机制场景喂一份静态 statuses，hero 活图喂逐波推进的动态 statuses（布局不依赖 statuses，
 * 故只算一次、状态变化只重绘节点/连线/粒子，与 GraphView 同源行为）。
 */
export function EmbeddedGraphCanvas({
  nodes,
  edges,
  layoutKind,
  statuses,
}: {
  nodes: PreviewNode[];
  edges: GraphEdge[];
  layoutKind: ElkGraphLayout;
  statuses: Record<string, RunStatus>;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [colWidth, setColWidth] = useState(0);
  const [layout, setLayout] = useState<LayoutResult | null>(null);
  const [inst, setInst] = useState<ReactFlowInstance | null>(null);

  // 实测内嵌画布宽度（= 阅读列宽），fit-to-width 据此缩放。
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    setColWidth(el.clientWidth);
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w > 0) setColWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 真实 ELK 布局（含收紧间距）；considerModelOrder 让辩论正反分带，并把端点（用户
  // 输入 / CEO 汇聚点）钉到首 / 末层，复刻 GraphView 的端点约束。布局只依赖形态，不依赖
  // statuses，故 hero 逐波改状态时不会重排。
  useEffect(() => {
    let cancelled = false;
    computeLayout(
      nodes.map((n) => n.id),
      edges,
      layoutKind,
      {
        source: nodes.find((n) => n.type === "userInput")?.id,
        sink: nodes.find((n) => n.type === "captain")?.id,
      },
    ).then((res) => {
      if (!cancelled) setLayout(res);
    });
    return () => {
      cancelled = true;
    };
  }, [nodes, edges, layoutKind]);

  const fit =
    layout && colWidth > 0
      ? fitWidthBox(layout.width, layout.height, colWidth)
      : null;

  // 与 GraphView 内嵌一致：只缩不放（宽/高均受限），居中。
  useEffect(() => {
    if (!inst || !layout || !fit) return;
    const x = Math.max(0, (colWidth - fit.renderedWidth) / 2);
    const y =
      fit.renderedHeight <= fit.height
        ? (fit.height - fit.renderedHeight) / 2
        : 0;
    inst.setViewport({ x, y, zoom: fit.zoom });
  }, [inst, layout, fit, colWidth]);

  const flowNodes = useMemo<Node[]>(() => {
    if (!layout) return [];
    // 左右流→连线锚点在左右；树形(DOWN)→锚点在上下，否则边会从节点侧面斜拉。
    const handleDirection = layoutKind === "tree" ? "vertical" : "horizontal";
    return nodes.map((n, i) => {
      const status = statuses[n.id] ?? (n.data.status as RunStatus);
      return {
        id: n.id,
        type: n.type,
        position: layout.positions[n.id] ?? { x: 0, y: 0 },
        data: {
          ...n.data,
          status,
          isAnimating: status === "running",
          handleDirection,
          enterIndex: i,
        },
      } as Node;
    });
  }, [nodes, layout, layoutKind, statuses]);

  const flowEdges = useMemo<Edge[]>(() => {
    return edges.map(
      (e) =>
        ({
          id: e.id,
          source: e.source,
          target: e.target,
          type: "step",
          data: {
            kind: e.kind ?? "dep",
            // 入边粒子流：目标节点运行中即点亮（与 GraphView 同规则）。
            animated: statuses[e.target] === "running",
          },
        }) as Edge,
    );
  }, [edges, statuses]);

  // 波次泳道（与 GraphView 同源）：喂 GraphRunLike（含 dependsOn），worker 列 ≥2 才出泳道。
  const waves = useMemo<WaveBand[]>(() => {
    if (!layout) return [];
    const captainId = nodes.find((n) => n.type === "captain")?.id ?? null;
    const dependsByTarget = new Map<string, string[]>();
    for (const e of edges) {
      if ((e.kind ?? "dep") !== "dep") continue;
      // 端点边（用户输入）不参与 worker 拓扑
      if (e.source.startsWith("__")) continue;
      const list = dependsByTarget.get(e.target) ?? [];
      list.push(e.source);
      dependsByTarget.set(e.target, list);
    }
    const runs = nodes
      .filter((n) => n.type === "agent" || n.type === "captain")
      .map((n) => ({
        id: n.id,
        dependsOn: dependsByTarget.get(n.id) ?? [],
        parentRunId:
          typeof n.data.parentRunId === "string" ? n.data.parentRunId : null,
        continuesRunId:
          typeof n.data.continuesRunId === "string"
            ? n.data.continuesRunId
            : null,
      }));
    return computeWaves(
      { runs } as unknown as Execution,
      layout.positions,
      { width: layout.width, height: layout.height },
      layoutKind,
      captainId,
    );
  }, [nodes, edges, layout, layoutKind]);

  const elkReady = Boolean(layout && colWidth > 0);

  return (
    <div
      ref={containerRef}
      className="relative overflow-hidden rounded-xl border border-border bg-card"
      style={{ height: fit?.height ?? EMBED_MIN_HEIGHT }}
      data-elk-ready={elkReady ? "true" : "false"}
    >
      {elkReady && (
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onInit={setInst}
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable={false}
          zoomOnScroll={false}
          zoomOnPinch={false}
          zoomOnDoubleClick={false}
          panOnDrag={false}
          preventScrolling={false}
          minZoom={0.05}
          proOptions={RF_PRO_OPTIONS}
        >
          <Background gap={20} size={1} />
          <WaveLanes waves={waves} />
        </ReactFlow>
      )}
      {/* 与 GraphView 内嵌一致：超过高度上限(520)时顶对齐 + 底部渐隐示意「还有更多」。 */}
      {fit?.overflowing && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-card to-transparent" />
      )}
    </div>
  );
}

/** 单个场景：标题 + 说明 + 静态 statuses 喂入共享画布。 */
export function ScenarioGraph({ scenario }: { scenario: Scenario }) {
  const statuses = useMemo(
    () =>
      Object.fromEntries(
        scenario.nodes.map((n) => [n.id, n.data.status as RunStatus]),
      ),
    [scenario],
  );
  return (
    <div>
      <h3 className="text-sm font-medium text-foreground">{scenario.title}</h3>
      <p className="mb-2 mt-0.5 text-xs leading-relaxed text-muted-foreground">
        {scenario.desc}
      </p>
      <EmbeddedGraphCanvas
        nodes={scenario.nodes}
        edges={scenario.edges}
        layoutKind={scenario.layout ?? "leftright"}
        statuses={statuses}
      />
    </div>
  );
}
