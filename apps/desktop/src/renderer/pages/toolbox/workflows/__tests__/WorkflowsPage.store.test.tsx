// @vitest-environment jsdom
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { emptyWorkflowDefinition } from "@/services/workflowDefinition";
import type { UserWorkflow } from "@/services/workflows";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/workflows", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/workflows")>();
  return {
    ...actual,
    listWorkflows: vi.fn(),
    createWorkflow: vi.fn(),
    deleteWorkflow: vi.fn(),
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

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

import {
  listMyWorkflowListings,
  publishWorkflow,
  publishWorkflowVersion,
  unpublishWorkflow,
} from "@/services/workflowStore";
import { listWorkflows } from "@/services/workflows";
import { MemoryRouter } from "react-router-dom";
import { WorkflowsPage } from "../WorkflowsPage";

const workflows = vi.mocked(listWorkflows);

const WORKFLOW: UserWorkflow = {
  id: "wf-1",
  name: "周报流水线",
  description: "每周写周报",
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
  vi.mocked(listMyWorkflowListings).mockReset();
  vi.mocked(listMyWorkflowListings).mockResolvedValue([]);
  vi.mocked(publishWorkflow).mockReset();
  vi.mocked(publishWorkflow).mockResolvedValue({
    id: "listing-1",
    name: "周报流水线",
    description: "每周写周报",
    author: "me",
    version: "1",
    installed: false,
    hasUpdate: false,
    workflowId: "wf-1",
    installWorkflowId: null,
    status: "published",
  });
  vi.mocked(publishWorkflowVersion).mockReset();
  vi.mocked(unpublishWorkflow).mockReset();
  vi.mocked(unpublishWorkflow).mockResolvedValue(undefined);
});

afterEach(cleanup);

describe("我的工作流上架入口", () => {
  it("行上有上架", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "上架" }));
    await waitFor(() => {
      expect(publishWorkflow).toHaveBeenCalledWith("wf-1");
    });
  });

  it("已上架的可下架", async () => {
    vi.mocked(listMyWorkflowListings).mockResolvedValue([
      {
        id: "listing-1",
        name: "周报流水线",
        description: "每周写周报",
        author: "me",
        version: "1",
        installed: false,
        hasUpdate: false,
        workflowId: "wf-1",
        installWorkflowId: null,
        status: "published",
      },
    ]);
    renderPage();
    expect(await screen.findByText("已上架")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "下架" }));
    await waitFor(() => {
      expect(unpublishWorkflow).toHaveBeenCalledWith("listing-1");
    });
  });

  it("作者下架后再上架走 POST /workflow-store，不走 /versions", async () => {
    vi.mocked(listMyWorkflowListings).mockResolvedValue([
      {
        id: "listing-1",
        name: "周报流水线",
        description: "每周写周报",
        author: "me",
        version: "1",
        installed: false,
        hasUpdate: false,
        workflowId: "wf-1",
        installWorkflowId: null,
        status: "unpublished",
      },
    ]);
    renderPage();
    await screen.findByText("周报流水线");
    expect(screen.queryByText("已上架")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "上架" }));
    await waitFor(() => {
      expect(publishWorkflow).toHaveBeenCalledWith("wf-1");
    });
    expect(publishWorkflowVersion).not.toHaveBeenCalled();
  });
});
