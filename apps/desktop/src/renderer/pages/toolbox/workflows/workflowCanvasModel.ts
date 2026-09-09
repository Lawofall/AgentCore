/**
 * definition ↔ 画布态的纯映射（无 React / 无 store），画布组件只管交互。
 *
 * 回写方向是保真面：definition 上除 nodes / edges 外还挂着 `slots` 与后端可能新加的
 * 顶层字段，这里必须原样带走——重新拼一个 `{nodes, edges}` = 用户拖一下节点就把它们抹了。
 */

import {
  type WorkflowDefNode,
  type WorkflowDefinition,
  createAgentStepNode,
  createHumanGateNode,
  workflowSlots,
} from "@/services/workflowDefinition";
import { type Edge, MarkerType, type Node } from "@xyflow/react";
import type { WorkflowCanvasNodeData } from "./workflowNodes";

const NODE_W = 200;
const NODE_H = 72;
const COL_GAP = 80;
const ROW_GAP = 28;

export function layoutPositions(
  count: number,
): Array<{ x: number; y: number }> {
  const cols = Math.max(1, Math.ceil(Math.sqrt(count)));
  return Array.from({ length: count }, (_, i) => ({
    x: (i % cols) * (NODE_W + COL_GAP) + 40,
    y: Math.floor(i / cols) * (NODE_H + ROW_GAP) + 40,
  }));
}

export function nodeTitle(n: WorkflowDefNode): string {
  if (n.kind === "human_gate") return n.label.trim() || "等人关卡";
  return n.role.trim() || "队员步骤";
}

export function nodeSubtitle(n: WorkflowDefNode): string | undefined {
  if (n.kind === "agent_step") {
    const t = n.task.trim();
    return t || undefined;
  }
  return undefined;
}

export function defToFlow(def: WorkflowDefinition): {
  nodes: Node<WorkflowCanvasNodeData>[];
  edges: Edge[];
} {
  const positions = layoutPositions(def.nodes.length);
  const slotLabels: Record<string, string> = {};
  for (const slot of workflowSlots(def)) slotLabels[slot.key] = slot.label;
  const nodes: Node<WorkflowCanvasNodeData>[] = def.nodes.map((n, i) => ({
    id: n.id,
    type: "workflowNode",
    position: positions[i] ?? { x: 40, y: 40 },
    data: {
      kind: n.kind,
      title: nodeTitle(n),
      subtitle: nodeSubtitle(n),
      slotLabels,
    },
  }));
  const edges: Edge[] = def.edges.map((e, i) => ({
    id: `e_${e.from}_${e.to}_${i}`,
    source: e.from,
    target: e.to,
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
  }));
  return { nodes, edges };
}

export function flowToDef(
  base: WorkflowDefinition,
  nodes: Node<WorkflowCanvasNodeData>[],
  edges: Edge[],
  defs: Map<string, WorkflowDefNode>,
): WorkflowDefinition {
  const outNodes: WorkflowDefNode[] = nodes.map((n) => {
    const prev = defs.get(n.id);
    if (prev) return prev;
    if (n.data.kind === "human_gate") {
      return createHumanGateNode({ id: n.id, label: n.data.title });
    }
    return createAgentStepNode({
      id: n.id,
      role: n.data.title,
      task: n.data.subtitle ?? "",
    });
  });
  const outEdges = edges
    .filter((e) => e.source && e.target)
    .map((e) => ({ from: e.source, to: e.target }));
  return { ...base, nodes: outNodes, edges: outEdges };
}

function slotLabelsEqual(
  a: Record<string, string> | undefined,
  b: Record<string, string> | undefined,
): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  for (const k of keys) {
    if (a[k] !== b[k]) return false;
  }
  return true;
}

function nodeDataEqual(
  a: WorkflowCanvasNodeData,
  b: WorkflowCanvasNodeData,
): boolean {
  return (
    a.kind === b.kind &&
    a.title === b.title &&
    a.subtitle === b.subtitle &&
    slotLabelsEqual(a.slotLabels, b.slotLabels)
  );
}

/**
 * Re-hydrate flow nodes from definition without breaking ReactFlow identity.
 * Same id + same data → keep the previous node (position / measured / selected).
 */
export function mergeFlowNodes(
  prev: Node<WorkflowCanvasNodeData>[],
  next: Node<WorkflowCanvasNodeData>[],
): Node<WorkflowCanvasNodeData>[] {
  if (prev.length === next.length) {
    const prevById = new Map(prev.map((n) => [n.id, n]));
    const same =
      next.every((n) => {
        const p = prevById.get(n.id);
        return p != null && nodeDataEqual(p.data, n.data);
      }) && prev.every((p, i) => p.id === next[i]?.id);
    if (same) return prev;
  }
  const old = new Map(prev.map((n) => [n.id, n]));
  return next.map((n) => {
    const p = old.get(n.id);
    if (!p) return n;
    return {
      ...n,
      position: p.position,
      selected: p.selected,
      width: p.width,
      height: p.height,
      measured: p.measured,
    };
  });
}

/** Same topology → keep previous edge array (StoreUpdater skips). */
export function mergeFlowEdges(prev: Edge[], next: Edge[]): Edge[] {
  if (
    prev.length === next.length &&
    prev.every(
      (e, i) =>
        e.id === next[i]?.id &&
        e.source === next[i]?.source &&
        e.target === next[i]?.target,
    )
  ) {
    return prev;
  }
  return next;
}
