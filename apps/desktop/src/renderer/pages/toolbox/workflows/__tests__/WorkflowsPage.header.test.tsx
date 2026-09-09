// @vitest-environment jsdom
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { emptyWorkflowDefinition } from "@/services/workflowDefinition";
import type { UserWorkflow } from "@/services/workflows";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/workflows", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/workflows")>();
  return {
    ...actual,
    listWorkflows: vi.fn(),
    createWorkflow: vi.fn(),
    deleteWorkflow: vi.fn(),
    createWorkflowFromPlaybook: vi.fn(),
    runWorkflow: vi.fn(),
    putWorkflowTrigger: vi.fn(),
    deleteWorkflowTrigger: vi.fn(),
    rotateWorkflowTriggerSecret: vi.fn(),
  };
});

vi.mock("@/services/workflowStore", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/workflowStore")>();
  return {
    ...actual,
    listMyWorkflowListings: vi.fn(async () => []),
    listInstalledWorkflows: vi.fn(async () => []),
    publishWorkflow: vi.fn(),
    publishWorkflowVersion: vi.fn(),
    unpublishWorkflow: vi.fn(),
  };
});

vi.mock("@/services/folders", () => ({ listFolders: vi.fn(async () => []) }));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

import { ApiError } from "@/services/api";
import { listWorkflows } from "@/services/workflows";
import { MemoryRouter } from "react-router-dom";
import { WorkflowsPage } from "../WorkflowsPage";

const workflows = vi.mocked(listWorkflows);

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
});

afterEach(cleanup);

describe("工作流列表 · 嵌入我的", () => {
  it("主 CTA 在内容区，不再自带页头", async () => {
    renderPage();
    await screen.findByText("周报流水线");

    expect(screen.getByRole("button", { name: "新建工作流" })).toBeTruthy();
    expect(
      screen.queryByRole("heading", { level: 1, name: "工作流" }),
    ).toBeNull();
    expect(screen.queryByRole("link", { name: "工具箱" })).toBeNull();
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
});
