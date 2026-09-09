/**
 * 画布回写保真：拖节点 / 连线只该动 nodes 与 edges。
 * definition 上的 `slots`（还有后端新加的顶层字段）在这里掉了，用户点一次保存就没了。
 */
import type {
  WorkflowDefNode,
  WorkflowDefinition,
} from "@/services/workflowDefinition";
import { describe, expect, it } from "vitest";
import {
  defToFlow,
  flowToDef,
  mergeFlowEdges,
  mergeFlowNodes,
} from "../workflowCanvasModel";

const DEFINITION: WorkflowDefinition = {
  nodes: [
    {
      id: "step1",
      kind: "agent_step",
      role: "调研员",
      task: "调研 {{topic}} 的定价",
    },
    { id: "gate1", kind: "human_gate", label: "审初稿" },
  ],
  edges: [{ from: "step1", to: "gate1" }],
  slots: [{ key: "topic", label: "调研主题", default: "Notion 的协作功能" }],
  future_policy: { level: 2 },
};

function defMap(def: WorkflowDefinition): Map<string, WorkflowDefNode> {
  return new Map(def.nodes.map((n) => [n.id, n]));
}

describe("workflowCanvasModel", () => {
  it("回写保留 slots 与顶层未知字段", () => {
    const flow = defToFlow(DEFINITION);
    const next = flowToDef(
      DEFINITION,
      flow.nodes,
      flow.edges,
      defMap(DEFINITION),
    );

    expect(next).toEqual(DEFINITION);
  });

  it("删掉一个节点也只动 nodes / edges", () => {
    const flow = defToFlow(DEFINITION);
    const next = flowToDef(
      DEFINITION,
      flow.nodes.filter((n) => n.id !== "gate1"),
      [],
      defMap(DEFINITION),
    );

    expect(next.nodes.map((n) => n.id)).toEqual(["step1"]);
    expect(next.edges).toEqual([]);
    expect(next.slots).toEqual(DEFINITION.slots);
    expect(next.future_policy).toEqual({ level: 2 });
  });

  it("节点带上槽位名称表，占位符才能画成参数名", () => {
    const flow = defToFlow(DEFINITION);

    expect(flow.nodes[0]?.data.slotLabels).toEqual({ topic: "调研主题" });
    expect(flow.nodes[0]?.data.subtitle).toBe("调研 {{topic}} 的定价");
  });

  it("没有槽位的工作流不长出 slots 键（旧数据逐字不变）", () => {
    const plain: WorkflowDefinition = {
      nodes: [{ id: "s", kind: "agent_step", role: "写手", task: "写稿" }],
      edges: [],
    };
    const flow = defToFlow(plain);

    expect(flowToDef(plain, flow.nodes, flow.edges, defMap(plain))).toEqual(
      plain,
    );
  });

  it("同内容再投影时 merge 保住节点引用（StoreUpdater 才能跳过）", () => {
    const a = defToFlow(DEFINITION);
    const b = defToFlow({ ...DEFINITION });
    expect(a.nodes).not.toBe(b.nodes);
    expect(mergeFlowNodes(a.nodes, b.nodes)).toBe(a.nodes);
    expect(mergeFlowEdges(a.edges, b.edges)).toBe(a.edges);
  });

  it("改任务文案时换 data、拖过的坐标保留", () => {
    const a = defToFlow(DEFINITION);
    const moved = a.nodes[0];
    if (!moved) throw new Error("expected node");
    a.nodes[0] = {
      ...moved,
      position: { x: 99, y: 88 },
      measured: { width: 200, height: 72 },
    };
    const edited: WorkflowDefinition = {
      ...DEFINITION,
      nodes: [
        {
          id: "step1",
          kind: "agent_step",
          role: "调研员",
          task: "调研 {{topic}} 的竞品",
        },
        { id: "gate1", kind: "human_gate", label: "审初稿" },
      ],
    };
    const b = defToFlow(edited);
    const merged = mergeFlowNodes(a.nodes, b.nodes);
    expect(merged).not.toBe(a.nodes);
    expect(merged[0]?.position).toEqual({ x: 99, y: 88 });
    expect(merged[0]?.measured).toEqual({ width: 200, height: 72 });
    expect(merged[0]?.data.subtitle).toBe("调研 {{topic}} 的竞品");
  });
});
