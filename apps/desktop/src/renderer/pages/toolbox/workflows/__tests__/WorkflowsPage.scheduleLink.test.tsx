// @vitest-environment jsdom
import { readScheduleFromWorkflow } from "@/pages/toolbox/automations/scheduleFromWorkflow";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { emptyWorkflowDefinition } from "@/services/workflowDefinition";
import type { UserWorkflow } from "@/services/workflows";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

import { listWorkflows } from "@/services/workflows";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
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

/** 落地页只回显 query，让断言看的是深链本身而不是自动化页的实现。 */
function AutomationsProbe() {
  const { search } = useLocation();
  return <div data-testid="automations-search">{search}</div>;
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.workflows.root]}>
      <Routes>
        <Route
          path={APP_PATHS.toolbox.workflows.root}
          element={<WorkflowsPage />}
        />
        <Route
          path={APP_PATHS.toolbox.automations.root}
          element={<AutomationsProbe />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  workflows.mockReset();
  workflows.mockResolvedValue([WORKFLOW]);
});

afterEach(() => {
  cleanup();
});

describe("工作流卡片 · 设为定时", () => {
  it("直达自动化，并把要绑定的工作流带过去", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "设为定时" }));

    const search = screen.getByTestId("automations-search").textContent ?? "";
    expect(readScheduleFromWorkflow(new URLSearchParams(search))).toEqual({
      workflowId: "wf-1",
      workflowName: "周报流水线",
    });
  });

  it("空态指向卡片上的入口，不再提内部词「站立任务」", async () => {
    workflows.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText("还没有工作流")).toBeTruthy();
    expect(screen.getByText(/设为定时/)).toBeTruthy();
    expect(screen.queryByText(/站立任务/)).toBeNull();
  });
});
