/**
 * 交付契约往返保真。
 *
 * 画布保存 = 把解析出来的 definition 原样 PATCH 回去，所以解析阶段丢掉的 deliverable
 * 字段（artifacts / required_sections / strict / citation_mode…）会在用户点一次保存后
 * 从后端记录里被抹掉。这里钉死：后端存什么，前端解析后就还是什么——唯独旧 `form`
 * （纯文字 / 文档 / 改工程）丢掉，不做兼容翻译。
 */
import { describe, expect, it } from "vitest";
import { parseWorkflowDefinition } from "../workflowDefinition";
import { toUserWorkflow } from "../workflows";

/** 服务端 Deliverable 的全字段（agentcore/runtime/runs/types.py），含已撤的 form。 */
const FULL_DELIVERABLE = {
  form: "files",
  output_format: "json",
  required_sections: ["结论", "证据", "风险"],
  artifacts: ["docs/report.md"],
  artifact_dir: "docs",
  strict: true,
  citation_mode: "two_phase",
};

/** 画布解析后应留下的契约：form 已丢掉。 */
const CONTRACT_REST = {
  output_format: "json",
  required_sections: ["结论", "证据", "风险"],
  artifacts: ["docs/report.md"],
  artifact_dir: "docs",
  strict: true,
  citation_mode: "two_phase",
};

function definitionWith(deliverable: unknown) {
  return {
    nodes: [
      {
        id: "step1",
        kind: "agent_step",
        role: "研究员",
        task: "写审计报告",
        deliverable,
      },
    ],
    edges: [],
  };
}

function firstDeliverable(raw: unknown) {
  const def = parseWorkflowDefinition(raw);
  const node = def.nodes[0];
  return node?.kind === "agent_step" ? node.deliverable : undefined;
}

describe("parseWorkflowDefinition · deliverable 保真", () => {
  it("丢掉 form，其余字段照留", () => {
    expect(firstDeliverable(definitionWith(FULL_DELIVERABLE))).toEqual(
      CONTRACT_REST,
    );
  });

  it("解析 → 序列化 → 再解析仍逐字相同（画布保存往返）", () => {
    const once = parseWorkflowDefinition(definitionWith(FULL_DELIVERABLE));
    const patched = JSON.parse(JSON.stringify(once)) as unknown;
    expect(parseWorkflowDefinition(patched)).toEqual(once);
    const node = once.nodes[0];
    expect(node?.kind === "agent_step" && node.deliverable).toEqual(
      CONTRACT_REST,
    );
  });

  it("wire → domain（列表 / 详情读取）同样丢掉 form、不丢其余字段", () => {
    const w = toUserWorkflow({
      id: "wf-1",
      name: "审查报告",
      description: null,
      definition: definitionWith(FULL_DELIVERABLE),
      version: 3,
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-01T01:00:00Z",
    });
    const node = w.definition.nodes[0];
    expect(node?.kind === "agent_step" && node.deliverable).toEqual(
      CONTRACT_REST,
    );
  });

  it("解析时丢掉 form 键，无论它是字符串还是别的", () => {
    expect(
      firstDeliverable(definitionWith({ form: "prose", artifacts: ["a.md"] })),
    ).toEqual({ artifacts: ["a.md"] });
    expect(
      firstDeliverable(
        definitionWith({ form: { legacy: true }, artifacts: ["a.md"] }),
      ),
    ).toEqual({ artifacts: ["a.md"] });
  });

  it("未声明 / 非对象 deliverable 仍是 undefined", () => {
    expect(firstDeliverable(definitionWith(undefined))).toBeUndefined();
    expect(firstDeliverable(definitionWith(null))).toBeUndefined();
    expect(firstDeliverable(definitionWith("files"))).toBeUndefined();
    expect(firstDeliverable(definitionWith(["files"]))).toBeUndefined();
  });

  it("后端新增的未知字段也透传（前端不做字段白名单）", () => {
    expect(
      firstDeliverable(
        definitionWith({ form: "prose", future_gate: { level: 2 } }),
      ),
    ).toEqual({ future_gate: { level: 2 } });
  });
});
