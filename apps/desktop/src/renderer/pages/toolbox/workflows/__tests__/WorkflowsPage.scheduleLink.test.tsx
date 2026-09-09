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
    listWorkflowTemplates: vi.fn(async () => []),
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

import { type FolderMeta, listFolders } from "@/services/folders";
import {
  deleteWorkflowTrigger,
  listWorkflows,
  putWorkflowTrigger,
  rotateWorkflowTriggerSecret,
} from "@/services/workflows";
import { MemoryRouter } from "react-router-dom";
import { WorkflowsPage } from "../WorkflowsPage";

const workflows = vi.mocked(listWorkflows);
const putTrigger = vi.mocked(putWorkflowTrigger);
const deleteTrigger = vi.mocked(deleteWorkflowTrigger);
const rotateSecret = vi.mocked(rotateWorkflowTriggerSecret);
const folders = vi.mocked(listFolders);

const WORKFLOW: UserWorkflow = {
  id: "wf-1",
  name: "周报流水线",
  description: null,
  definition: emptyWorkflowDefinition(),
  source: null,
  trigger: null,
  version: 3,
  createdAt: "2026-08-01T00:00:00Z",
  updatedAt: "2026-08-01T00:00:00Z",
};

function cloudFolder(id: string, name: string): FolderMeta {
  return { id, name, mode: "cloud", localRootId: null, localSubpath: null };
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.workflows.root]}>
      <WorkflowsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  workflows.mockReset();
  putTrigger.mockReset();
  deleteTrigger.mockReset();
  rotateSecret.mockReset();
  folders.mockReset();
  workflows.mockResolvedValue([WORKFLOW]);
  folders.mockResolvedValue([cloudFolder("fold-cloud", "云桌")]);
});

afterEach(cleanup);

describe("工作流行 · 设为定时", () => {
  it("打开本页对话框，不跳走", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "设为定时" }));

    expect(await screen.findByText(/设为定时 · 周报流水线/)).toBeTruthy();
    expect(screen.getByRole("tablist", { name: "触发方式" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "定时" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Webhook" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "立即跑" })).toBeNull();
    expect(screen.queryByRole("button", { name: "立即触发" })).toBeNull();
  });

  it("保存定时后行上出现触发摘要", async () => {
    putTrigger.mockResolvedValue({
      ...WORKFLOW,
      trigger: {
        kind: "schedule",
        schedulePreset: "weekly_mon",
        cron: null,
        folderId: "fold-cloud",
        enabled: true,
        nextRunAt: null,
        lastRunAt: null,
        lastError: null,
        webhookId: null,
        webhookUrl: null,
        webhookSecret: null,
      },
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "设为定时" }));
    expect(await screen.findByText("云桌")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(putTrigger).toHaveBeenCalledWith(
        "wf-1",
        expect.objectContaining({
          kind: "schedule",
          folderId: "fold-cloud",
        }),
      );
    });
    expect(await screen.findByText(/每周一/)).toBeTruthy();
    expect(screen.queryByText(/设为定时 · 周报流水线/)).toBeNull();
  });

  it("已有触发时可以清除", async () => {
    workflows.mockResolvedValue([
      {
        ...WORKFLOW,
        trigger: {
          kind: "schedule",
          schedulePreset: "daily",
          cron: null,
          folderId: "fold-cloud",
          enabled: true,
          nextRunAt: null,
          lastRunAt: null,
          lastError: "timeout",
          webhookId: null,
          webhookUrl: null,
          webhookSecret: null,
        },
      },
    ]);
    deleteTrigger.mockResolvedValue({ ...WORKFLOW, trigger: null });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    expect(await screen.findByText(/每天/)).toBeTruthy();
    expect(screen.getByText(/上次没跑成/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "设为定时" }));
    fireEvent.click(await screen.findByRole("button", { name: "清除" }));

    await waitFor(() => {
      expect(deleteTrigger).toHaveBeenCalledWith("wf-1");
    });
    confirm.mockRestore();
  });

  it("Webhook 可轮换密钥", async () => {
    workflows.mockResolvedValue([
      {
        ...WORKFLOW,
        trigger: {
          kind: "webhook",
          schedulePreset: null,
          cron: null,
          folderId: "fold-cloud",
          enabled: true,
          nextRunAt: null,
          lastRunAt: null,
          lastError: null,
          webhookId: "wh-1",
          webhookUrl: "/v1/hooks/workflows/wh-1",
          webhookSecret: null,
        },
      },
    ]);
    rotateSecret.mockResolvedValue({
      webhookSecret: "sec_rotated",
      webhookUrl: "/v1/hooks/workflows/wh-1",
      webhookId: "wh-1",
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "设为定时" }));
    expect(
      await screen.findByDisplayValue("/v1/hooks/workflows/wh-1"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "轮换密钥" }));

    await waitFor(() => {
      expect(rotateSecret).toHaveBeenCalledWith("wf-1");
    });
    expect(await screen.findByDisplayValue("sec_rotated")).toBeTruthy();
    confirm.mockRestore();
  });

  it("空态只留去市场，不教还没出现的行上动作", async () => {
    workflows.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText("还没有工作流")).toBeTruthy();
    expect(screen.getByRole("button", { name: "去市场" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "新建工作流" })).toBeTruthy();
    expect(screen.queryByText(/设为定时/)).toBeNull();
    expect(screen.queryByText(/站立任务/)).toBeNull();
  });
});
