// @vitest-environment jsdom
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { emptyWorkflowDefinition } from "@/services/workflowDefinition";
import type { UserWorkflow } from "@/services/workflows";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/workflows", () => ({
  listWorkflows: vi.fn(),
  listWorkflowTemplates: vi.fn(async () => []),
  createWorkflow: vi.fn(),
  deleteWorkflow: vi.fn(),
  createWorkflowFromPlaybook: vi.fn(),
  runWorkflow: vi.fn(),
}));

vi.mock("@/services/folders", () => ({ listFolders: vi.fn(async () => []) }));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

import { ApiError } from "@/services/api";
import { listWorkflowTemplates, listWorkflows } from "@/services/workflows";
import { MemoryRouter } from "react-router-dom";
import { WorkflowsPage } from "../WorkflowsPage";

const workflows = vi.mocked(listWorkflows);
const templates = vi.mocked(listWorkflowTemplates);

const WORKFLOW: UserWorkflow = {
  id: "wf-1",
  name: "周报流水线",
  description: null,
  definition: emptyWorkflowDefinition(),
  source: null,
  version: 3,
  createdAt: "2026-08-01T00:00:00Z",
  updatedAt: "2026-08-01T00:00:00Z",
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.workflows.root]}>
      <WorkflowsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  workflows.mockReset();
  workflows.mockResolvedValue([WORKFLOW]);
  templates.mockReset();
  templates.mockResolvedValue([]);
});

afterEach(cleanup);

describe("工作流列表 · 统一页头", () => {
  it("主 CTA 进页头动作位，返回工具箱并挂本页标题", async () => {
    const { container } = renderPage();
    await screen.findByText("周报流水线");

    const header = container.querySelector("header");
    expect(screen.getAllByRole("link", { name: "工具箱" })).toHaveLength(1);
    expect(
      screen.getByRole("heading", { level: 1, name: "工作流" }),
    ).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "工具箱能力" })).toBeNull();
    expect(
      header?.contains(screen.getByRole("button", { name: "新建工作流" })),
    ).toBe(true);
  });

  it("页头与内容区都无说明书", async () => {
    renderPage();
    await screen.findByText("周报流水线");
    expect(screen.queryByText(/可保存的团队拆法/)).toBeNull();
  });
});

describe("工作流列表 · 可恢复失败", () => {
  it("我的工作流加载失败走 muted 行内文案", async () => {
    workflows.mockRejectedValue(
      new ApiError(500, JSON.stringify({ error: { message: "列表开小差" } })),
    );
    renderPage();

    const err = await screen.findByText("列表开小差");
    expect(err.className).toContain("text-muted-foreground");
    expect(err.className).not.toContain("destructive");
  });

  it("官方模板加载失败走 muted 行内文案", async () => {
    templates.mockRejectedValue(
      new ApiError(
        500,
        JSON.stringify({ error: { message: "官方模板开小差" } }),
      ),
    );
    renderPage();

    const err = await screen.findByText("官方模板开小差");
    expect(err.className).toContain("text-muted-foreground");
    expect(err.className).not.toContain("destructive");
  });
});
