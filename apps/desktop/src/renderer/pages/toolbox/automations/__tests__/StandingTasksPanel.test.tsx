// @vitest-environment jsdom
import { ApiError } from "@/services/api";
import type { FolderMeta } from "@/services/folders";
import type { StandingTask } from "@/services/standingTasks";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/folders", () => ({ listFolders: vi.fn() }));

vi.mock("@/services/standingTasks", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/standingTasks")>();
  return {
    ...actual,
    listStandingTasks: vi.fn(),
    listStandingTaskTemplates: vi.fn(async () => []),
  };
});

vi.mock("@/services/workflows", () => ({
  listWorkflowOptions: vi.fn(async () => []),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

import { listFolders } from "@/services/folders";
import { listStandingTasks } from "@/services/standingTasks";
import { MemoryRouter } from "react-router-dom";
import { StandingTasksPanel } from "../StandingTasksPanel";
import { scheduleFromWorkflowPath } from "../scheduleFromWorkflow";

const folders = vi.mocked(listFolders);
const tasks = vi.mocked(listStandingTasks);

const CLOUD_FOLDER: FolderMeta = {
  id: "folder-1",
  name: "工作",
  mode: "cloud",
  localRootId: null,
  localSubpath: null,
};

const BOUND_TASK: StandingTask = {
  id: "task-1",
  name: "竞品简报",
  triggerKind: "schedule",
  schedulePreset: "weekly_mon",
  cron: null,
  folderId: "folder-1",
  goal: "",
  permissionAxes: {
    file_write: "session",
    command: "auto",
    host: "session",
  },
  enabled: true,
  nextRunAt: null,
  conversationId: null,
  lastRunAt: null,
  webhookId: null,
  webhookUrl: null,
  webhookSecret: null,
  templateKey: null,
  templateConfig: {},
  workflowId: "wf-1",
  workflowName: "周报流水线",
  createdAt: "2026-08-01T00:00:00Z",
  updatedAt: "2026-08-01T00:00:00Z",
};

function renderPanel(entry = "/toolbox/automations") {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <StandingTasksPanel />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  folders.mockReset();
  tasks.mockReset();
  folders.mockResolvedValue([CLOUD_FOLDER]);
  tasks.mockResolvedValue([BOUND_TASK]);
});

afterEach(() => {
  cleanup();
});

describe("StandingTasksPanel", () => {
  it("names the bound workflow on the row and links back to its canvas", async () => {
    renderPanel();

    const chip = await screen.findByRole("link", { name: /周报流水线/ });
    expect(chip.getAttribute("href")).toBe("/toolbox/workflows/wf-1");
  });

  it("reports a failed folder request instead of implying there is no workspace", async () => {
    folders.mockRejectedValueOnce(
      new ApiError(503, JSON.stringify({ detail: "服务暂时不可用" })),
    );
    renderPanel();

    const note = await screen.findByText(/读不到文件夹列表/);
    expect(note.parentElement?.className).toContain("bg-muted/40");
    expect(note.parentElement?.className).not.toContain("destructive");
    expect(screen.getByText(/这不代表你没有云端文件夹/)).toBeTruthy();
    // The task list itself still loaded.
    expect(screen.getByText("竞品简报")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() =>
      expect(screen.queryByText(/读不到文件夹列表/)).toBeNull(),
    );
    expect(folders).toHaveBeenCalledTimes(2);
  });

  it("shows a recoverable task-list failure as muted inline text", async () => {
    tasks.mockRejectedValueOnce(
      new ApiError(
        503,
        JSON.stringify({ error: { message: "任务列表开小差" } }),
      ),
    );
    renderPanel();

    const err = await screen.findByText("任务列表开小差");
    expect(err.className).toContain("text-muted-foreground");
    expect(err.className).not.toContain("destructive");
  });

  it("shows 立即触发 rather than the workflow's 跑一次 wording", async () => {
    renderPanel();

    expect(
      await screen.findByRole("button", { name: "立即触发" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "跑一次" })).toBeNull();
  });
});

describe("StandingTasksPanel 设为定时 deep link", () => {
  it("opens a create drawer already bound to the workflow", async () => {
    renderPanel(scheduleFromWorkflowPath({ id: "wf-9", name: "周一竞品简报" }));

    const binder = (await screen.findByLabelText(
      /绑定工作流/,
    )) as HTMLSelectElement;
    await waitFor(() => expect(binder.value).toBe("wf-9"));
    // The options request has not landed (mocked empty) — the picker must still
    // name the binding instead of rendering a blank row.
    expect(screen.getByRole("option", { name: "周一竞品简报" })).toBeTruthy();

    const name = screen.getByLabelText(/^名称/) as HTMLInputElement;
    expect(name.value).toBe("周一竞品简报");

    // Bound = the goal is an optional per-run supplement, so 创建 is reachable
    // right away: land → pick a schedule → done.
    const folderSelect = screen.getByLabelText(
      /^云端文件夹/,
    ) as HTMLSelectElement;
    await waitFor(() => expect(folderSelect.value).toBe("folder-1"));
    expect(
      screen.getByRole("button", { name: "创建" }).hasAttribute("disabled"),
    ).toBe(false);
  });

  it("drops the binding once the drawer is dismissed", async () => {
    renderPanel(scheduleFromWorkflowPath({ id: "wf-9", name: "周一竞品简报" }));

    fireEvent.click(await screen.findByRole("button", { name: "取消" }));

    await waitFor(() =>
      expect(screen.queryByLabelText(/绑定工作流/)).toBeNull(),
    );

    fireEvent.click(screen.getByRole("button", { name: "新建" }));

    const binder = (await screen.findByLabelText(
      /绑定工作流/,
    )) as HTMLSelectElement;
    expect(binder.value).toBe("");
  });
});
