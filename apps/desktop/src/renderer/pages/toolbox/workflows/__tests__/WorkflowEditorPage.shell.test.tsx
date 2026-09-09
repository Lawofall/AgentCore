// @vitest-environment jsdom
/**
 * 工作流编辑全屏壳：标题在顶栏、选中才出右侧浮坞、from=board 回到白板。
 */
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import type { WorkflowDefinition } from "@/services/workflowDefinition";
import type { UserWorkflow } from "@/services/workflows";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/workflows", () => ({
  getWorkflow: vi.fn(),
  patchWorkflow: vi.fn(),
  runWorkflow: vi.fn(),
  suggestWorkflowSlots: vi.fn(),
}));
vi.mock("@/services/folders", () => ({ listFolders: vi.fn(async () => []) }));
vi.mock("@/lib/toast", () => ({ notifySuccess: vi.fn() }));
vi.mock("../WorkflowCanvas", () => ({
  WorkflowCanvas: ({
    definition,
    onSelect,
  }: {
    definition: WorkflowDefinition;
    onSelect: (id: string | null) => void;
  }) => (
    <div data-testid="canvas">
      <button
        type="button"
        onClick={() => onSelect(definition.nodes[0]?.id ?? null)}
      >
        选中节点
      </button>
      <button type="button" onClick={() => onSelect(null)}>
        取消选中
      </button>
    </div>
  ),
}));

import { getWorkflow } from "@/services/workflows";
import { WorkflowEditorPage } from "../WorkflowEditorPage";

const load = vi.mocked(getWorkflow);

function workflow(): UserWorkflow {
  return {
    id: "wf-1",
    name: "竞品调研",
    description: "可保存的团队拆法",
    definition: {
      nodes: [
        {
          id: "step-1",
          kind: "agent_step",
          role: "研究员",
          task: "扫一遍竞品动态",
        },
      ],
      edges: [],
    },
    source: null,
    version: 2,
    createdAt: "2026-08-01T00:00:00Z",
    updatedAt: "2026-08-01T00:00:00Z",
  };
}

function Landed() {
  const { pathname } = useLocation();
  return <div data-testid="landed">{pathname}</div>;
}

function renderAt(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route
          path={APP_PATHS.toolbox.workflows.edit(":workflowId")}
          element={<WorkflowEditorPage />}
        />
        <Route path={APP_PATHS.toolbox.workflows.root} element={<Landed />} />
        <Route path="/whiteboard/:boardId" element={<Landed />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  load.mockReset();
  load.mockResolvedValue(workflow());
});

afterEach(() => {
  cleanup();
});

describe("WorkflowEditorPage · 全屏壳", () => {
  it("未选中时浮坞不在，也没有整页名称表单 label", async () => {
    renderAt(APP_PATHS.toolbox.workflows.edit("wf-1"));

    expect(await screen.findByTestId("canvas")).toBeTruthy();
    expect(screen.getByLabelText("工作流标题")).toBeTruthy();
    expect(screen.getByDisplayValue("竞品调研")).toBeTruthy();
    expect(
      screen.queryByRole("complementary", { name: "工作流属性" }),
    ).toBeNull();
    expect(screen.queryByLabelText("角色")).toBeNull();
    expect(screen.queryByLabelText("说明（可选）")).toBeNull();
    expect(screen.queryByRole("textbox", { name: "名称" })).toBeNull();
  });

  it("选中后坞内能改节点", async () => {
    renderAt(APP_PATHS.toolbox.workflows.edit("wf-1"));
    await screen.findByTestId("canvas");

    fireEvent.click(screen.getByRole("button", { name: "选中节点" }));

    expect(
      screen.getByRole("complementary", { name: "工作流属性" }),
    ).toBeTruthy();
    const role = screen.getByLabelText("角色") as HTMLInputElement;
    expect(role.value).toBe("研究员");
    fireEvent.change(role, { target: { value: "分析员" } });
    expect((screen.getByLabelText("角色") as HTMLInputElement).value).toBe(
      "分析员",
    );

    fireEvent.click(screen.getByRole("button", { name: "取消选中" }));
    expect(
      screen.queryByRole("complementary", { name: "工作流属性" }),
    ).toBeNull();
  });

  it("from=board 返回白板", async () => {
    renderAt(
      `${APP_PATHS.toolbox.workflows.edit("wf-1")}?from=board&boardId=board-9`,
    );
    await screen.findByTestId("canvas");

    fireEvent.click(screen.getByRole("button", { name: "返回白板" }));
    expect(screen.getByTestId("landed").textContent).toBe(
      "/whiteboard/board-9",
    );
  });

  it("默认返回工作流列表", async () => {
    renderAt(APP_PATHS.toolbox.workflows.edit("wf-1"));
    await screen.findByTestId("canvas");

    fireEvent.click(screen.getByRole("button", { name: "返回工作流列表" }));
    expect(screen.getByTestId("landed").textContent).toBe(
      APP_PATHS.toolbox.workflows.root,
    );
  });
});
