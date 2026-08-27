import { ApiError, api } from "@/services/api";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createAgentStepNode,
  createHumanGateNode,
  emptyWorkflowDefinition,
  isWorkflowConnectionAllowed,
  parseWorkflowDefinition,
  validateWorkflowDefinition,
} from "../workflowDefinition";
import {
  createWorkflowFromPlaybook,
  listWorkflowTemplates,
  listWorkflows,
  runWorkflow,
  toUserWorkflow,
  toWorkflowTemplate,
} from "../workflows";

vi.mock("@/services/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api")>();
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  };
});

const apiGet = vi.mocked(api.get);
const apiPost = vi.mocked(api.post);
const apiPatch = vi.mocked(api.patch);

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
});

describe("workflowDefinition", () => {
  it("accepts a simple linear graph", () => {
    const a = createAgentStepNode({
      id: "n1",
      role: "调研员",
      task: "收集竞品",
    });
    const g = createHumanGateNode({ id: "n2", label: "审初稿" });
    const issues = validateWorkflowDefinition({
      nodes: [a, g],
      edges: [{ from: "n1", to: "n2" }],
    });
    expect(issues).toEqual([]);
  });

  it("rejects cycles and empty agent fields", () => {
    const a = createAgentStepNode({ id: "n1", role: "", task: "" });
    const b = createAgentStepNode({
      id: "n2",
      role: "写手",
      task: "写稿",
    });
    const issues = validateWorkflowDefinition({
      nodes: [a, b],
      edges: [
        { from: "n1", to: "n2" },
        { from: "n2", to: "n1" },
      ],
    });
    const codes = issues.map((i) => i.code);
    expect(codes).toContain("empty_role");
    expect(codes).toContain("empty_task");
    expect(codes).toContain("cycle");
  });

  it("rejects human_gate→human_gate and orphan gate→agent", () => {
    const a = createAgentStepNode({ id: "a", role: "A", task: "ta" });
    const g1 = createHumanGateNode({ id: "g1", label: "审1" });
    const g2 = createHumanGateNode({ id: "g2", label: "审2" });
    const b = createAgentStepNode({ id: "b", role: "B", task: "tb" });
    const chained = validateWorkflowDefinition({
      nodes: [a, g1, g2, b],
      edges: [
        { from: "a", to: "g1" },
        { from: "g1", to: "g2" },
        { from: "g2", to: "b" },
      ],
    });
    expect(chained.map((i) => i.code)).toContain("gate_to_gate");

    const orphanGate = createHumanGateNode({ id: "g", label: "孤门" });
    const orphan = validateWorkflowDefinition({
      nodes: [orphanGate, b],
      edges: [{ from: "g", to: "b" }],
    });
    expect(orphan.map((i) => i.code)).toContain("gate_without_agent_pred");

    expect(
      isWorkflowConnectionAllowed(
        { nodes: [a, g1, g2], edges: [{ from: "a", to: "g1" }] },
        "g1",
        "g2",
      ),
    ).toBe(false);
    expect(
      isWorkflowConnectionAllowed(
        { nodes: [a, g1, b], edges: [{ from: "a", to: "g1" }] },
        "g1",
        "b",
      ),
    ).toBe(true);
  });

  it("parseWorkflowDefinition drops unknown kinds", () => {
    const def = parseWorkflowDefinition({
      nodes: [
        { id: "n1", kind: "agent_step", role: "A", task: "T" },
        { id: "x", kind: "start" },
      ],
      edges: [{ from: "n1", to: "x" }],
    });
    expect(def.nodes).toHaveLength(1);
    expect(def.edges).toHaveLength(1);
  });
});

describe("workflows client", () => {
  it("maps wire → domain", () => {
    const w = toUserWorkflow({
      id: "wf-1",
      name: "三步质检",
      description: null,
      definition: emptyWorkflowDefinition(),
      version: 2,
      created_at: "2026-07-31T00:00:00Z",
      updated_at: "2026-07-31T01:00:00Z",
    });
    expect(w.name).toBe("三步质检");
    expect(w.version).toBe(2);
    expect(w.definition.nodes).toEqual([]);
  });

  it("surfaces API errors instead of a local shadow store", async () => {
    apiGet.mockRejectedValue(new ApiError(404, "not found"));
    await expect(listWorkflows()).rejects.toBeInstanceOf(ApiError);
  });

  it("uses remote when API responds", async () => {
    apiGet.mockResolvedValueOnce([
      {
        id: "wf-remote",
        name: "远程",
        description: null,
        definition: { nodes: [], edges: [] },
        version: 1,
        created_at: "2026-07-31T00:00:00Z",
        updated_at: "2026-07-31T00:00:00Z",
      },
    ]);
    const list = await listWorkflows();
    expect(list[0]?.id).toBe("wf-remote");
  });
});

describe("runWorkflow · 槽位覆盖", () => {
  beforeEach(() => {
    apiPost.mockResolvedValue({ conversation_id: "c1" });
  });

  it("没有覆盖时请求里不出现 slots（= 按 default 原样重跑）", async () => {
    await runWorkflow("wf-1", { folderId: "f1", slots: {} });
    expect(apiPost).toHaveBeenCalledWith("/v1/workflows/wf-1/run", {
      folder_id: "f1",
      note: null,
    });
  });

  it("只带用户改过的槽位，空值不占位", async () => {
    await runWorkflow("wf-1", {
      folderId: "f1",
      note: " 顺便看看定价 ",
      slots: { topic: " Linear 的项目视图 ", angle: "   " },
    });
    expect(apiPost).toHaveBeenCalledWith("/v1/workflows/wf-1/run", {
      folder_id: "f1",
      note: "顺便看看定价",
      slots: { topic: "Linear 的项目视图" },
    });
  });
});

describe("workflow templates / from-playbook (§10.8)", () => {
  it("maps template wire with slots", () => {
    const t = toWorkflowTemplate({
      id: "cite_write_review",
      title: "调研报告",
      summary: "成文专线",
      slots: [{ key: "topic", label: "主题", required: true, hint: "议题" }],
    });
    expect(t.id).toBe("cite_write_review");
    expect(t.title).toBe("调研报告");
    expect(t.slots).toEqual([
      {
        key: "topic",
        label: "主题",
        required: true,
        hint: "议题",
        choices: [],
      },
    ]);
  });

  it("keeps optional slots optional and carries their allowed values", () => {
    const t = toWorkflowTemplate({
      id: "build_app",
      title: "从零搭应用",
      summary: "脚手架→模块→联调",
      primary_slots: "app（必填，应用简述）；intensity（可选，编制档）",
      slots: [
        { key: "app", label: "应用", required: true, hint: "应用简述" },
        {
          key: "intensity",
          label: "编制",
          required: false,
          hint: "不填按 lean",
          choices: [
            { value: "lean", label: "瘦启动" },
            { value: "standard", label: "标准" },
          ],
        },
      ],
    });
    expect(t.slots.map((s) => s.required)).toEqual([true, false]);
    expect(t.slots[1]?.choices.map((c) => c.value)).toEqual([
      "lean",
      "standard",
    ]);
  });

  it("has no local slot replica: prose-only payload yields no slots", () => {
    const t = toWorkflowTemplate({
      id: "cite_write_review",
      title: "调研报告成文",
      summary: "成文专线",
      primary_slots: "topic（必填）",
    });
    expect(t.slots).toEqual([]);
  });

  it("listWorkflowTemplates returns empty on 404 (hide official section)", async () => {
    apiGet.mockRejectedValueOnce(new ApiError(404, "not found"));
    expect(await listWorkflowTemplates()).toEqual([]);
    expect(apiGet).toHaveBeenCalledWith("/v1/workflow-playbook-templates");
  });

  it("listWorkflowTemplates maps remote catalog", async () => {
    apiGet.mockResolvedValueOnce([
      {
        id: "map_fanout",
        title: "多角对齐摸底",
        summary: "N 路并行摸底",
        slots: [
          { key: "topic", label: "主题", required: true },
          { key: "angles", label: "方向", required: true },
        ],
      },
      {
        id: "build_app",
        title: "从零搭应用",
        summary: "脚手架→模块→联调",
        slots: [{ key: "app", label: "应用", required: true }],
      },
    ]);
    const list = await listWorkflowTemplates();
    expect(list.map((t) => t.id)).toEqual(["map_fanout", "build_app"]);
    expect(list[0]?.slots.map((s) => s.key)).toEqual(["topic", "angles"]);
    expect(list[1]?.slots.map((s) => s.key)).toEqual(["app"]);
  });

  it("createWorkflowFromPlaybook posts playbook + slots", async () => {
    apiPost.mockResolvedValueOnce({
      id: "wf-from-pb",
      name: "我的应用",
      description: null,
      definition: { nodes: [], edges: [] },
      version: 1,
      created_at: "2026-07-31T00:00:00Z",
      updated_at: "2026-07-31T00:00:00Z",
    });
    const created = await createWorkflowFromPlaybook({
      playbook: "build_app",
      name: "我的应用",
      slots: { app: "记账 SPA" },
    });
    expect(created.id).toBe("wf-from-pb");
    expect(apiPost).toHaveBeenCalledWith("/v1/workflows/from-playbook", {
      playbook: "build_app",
      name: "我的应用",
      slots: { app: "记账 SPA" },
    });
  });

  it("createWorkflowFromPlaybook surfaces 404 (backend-only expansion)", async () => {
    apiPost.mockRejectedValueOnce(new ApiError(404, "not found"));
    await expect(
      createWorkflowFromPlaybook({
        playbook: "cite_write_review",
        slots: { topic: "AI 监管" },
      }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
