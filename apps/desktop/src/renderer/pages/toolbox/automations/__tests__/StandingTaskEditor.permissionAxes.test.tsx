// @vitest-environment jsdom
import { ApiError } from "@/services/api";
import type { FolderMeta } from "@/services/folders";
import type { PermissionAxes } from "@/services/permissionAxes";
import { RECIPE_AXES } from "@/services/permissionAxes";
import type { StandingTask } from "@/services/standingTasks";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/standingTasks", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/standingTasks")>();
  return { ...actual, patchStandingTask: vi.fn(), createStandingTask: vi.fn() };
});

vi.mock("@/services/workflows", () => ({
  listWorkflowOptions: vi.fn(async () => [{ id: "wf-1", name: "周报流水线" }]),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

import {
  patchStandingTask,
  utcCronFromLocalHm,
} from "@/services/standingTasks";
import { MemoryRouter } from "react-router-dom";
import {
  StandingTaskEditorDrawer,
  emptyStandingTaskForm,
  formFromStandingTask,
} from "../StandingTaskEditor";

const patch = vi.mocked(patchStandingTask);

/** What the server installs for 每日对话复盘 — matches no built-in recipe. */
const TEMPLATE_AXES: PermissionAxes = {
  file_write: "ask",
  command: "ask",
  host: "ask",
};

const CLOUD_FOLDERS: FolderMeta[] = [
  {
    id: "folder-1",
    name: "工作",
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
  },
];

function makeTask(overrides: Partial<StandingTask> = {}): StandingTask {
  return {
    id: "task-1",
    name: "每日对话复盘",
    triggerKind: "schedule",
    schedulePreset: "custom",
    cron: "0 1 * * *",
    folderId: "folder-1",
    goal: "系统托管目标",
    permissionAxes: TEMPLATE_AXES,
    enabled: true,
    nextRunAt: null,
    conversationId: null,
    lastRunAt: null,
    webhookId: null,
    webhookUrl: null,
    webhookSecret: null,
    templateKey: "daily_conversation_review",
    templateConfig: { includeGlobal: true, folderIds: [], lookbackHours: 24 },
    workflowId: null,
    workflowName: null,
    createdAt: "2026-08-01T00:00:00Z",
    updatedAt: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

function renderEditor(task: StandingTask, foldersError: string | null = null) {
  render(
    <MemoryRouter>
      <StandingTaskEditorDrawer
        open
        mode="edit"
        initial={formFromStandingTask(task)}
        taskId={task.id}
        cloudFolders={foldersError ? [] : CLOUD_FOLDERS}
        foldersError={foldersError}
        onClose={() => {}}
        onSaved={async () => {}}
      />
    </MemoryRouter>,
  );
}

/** Axes the save request would actually apply (omitted field = server keeps its own). */
function savedAxes(task: StandingTask): PermissionAxes {
  expect(patch).toHaveBeenCalledTimes(1);
  return patch.mock.calls[0][1].permissionAxes ?? task.permissionAxes;
}

beforeEach(() => {
  patch.mockReset();
  patch.mockImplementation(async (_id, _input) => makeTask());
});

afterEach(() => {
  cleanup();
});

describe("StandingTaskEditorDrawer permission axes", () => {
  it("keeps the server's custom axes when the user only changes the time", async () => {
    const task = makeTask();
    renderEditor(task);

    fireEvent.change(screen.getByLabelText(/每天触发时间/), {
      target: { value: "07:30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    // The edit the user did make still goes through.
    expect(patch.mock.calls[0][1].cron).toBe(utcCronFromLocalHm(7, 30));
    expect(savedAxes(task)).toEqual(TEMPLATE_AXES);
    expect(savedAxes(task)).not.toEqual(RECIPE_AXES.less_interrupt);
  });

  it("shows the custom tuple instead of pretending it is a recipe", () => {
    renderEditor(makeTask());

    const select = screen.getByLabelText(/自主度/) as HTMLSelectElement;
    expect(select.value).toBe("custom");

    const custom = screen.getByRole("option", { name: /自定义/ });
    expect(custom.textContent).toMatch(/改文件每次确认/);
    expect(custom.textContent).toMatch(/执行每次确认/);
    expect(custom.textContent).toMatch(/本机每次确认/);
  });

  it("sends the new axes once the user picks a recipe on purpose", async () => {
    const task = makeTask();
    renderEditor(task);

    fireEvent.change(screen.getByLabelText(/自主度/), {
      target: { value: "cautious" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(savedAxes(task)).toEqual(RECIPE_AXES.cautious);
  });

  it("keeps custom axes on plain tasks too", async () => {
    const task = makeTask({
      id: "task-2",
      name: "竞品简报",
      templateKey: null,
      templateConfig: {},
      schedulePreset: "weekly_mon",
      cron: null,
      permissionAxes: {
        file_write: "session",
        command: "ask",
        host: "off",
      },
    });
    renderEditor(task);

    fireEvent.change(screen.getByLabelText(/^名称/), {
      target: { value: "竞品简报（周一）" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][1].name).toBe("竞品简报（周一）");
    expect(savedAxes(task)).toEqual(task.permissionAxes);
  });
});

describe("StandingTaskEditorDrawer folder loading", () => {
  it("says the workspace list failed instead of claiming there is no cloud workspace", () => {
    renderEditor(makeTask(), "工作区列表加载失败");

    const note = screen.getByText(/读不到工作区列表/);
    expect(note.className).toContain("text-muted-foreground");
    expect(note.className).not.toContain("destructive");
    expect(screen.queryByText(/没有可用的云工作区/)).toBeNull();
  });

  it("keeps the empty-cloud-workspace prompt primary (needs you)", async () => {
    render(
      <MemoryRouter>
        <StandingTaskEditorDrawer
          open
          mode="create"
          initial={emptyStandingTaskForm([])}
          taskId={null}
          cloudFolders={[]}
          foldersError={null}
          onClose={() => {}}
          onSaved={async () => {}}
        />
      </MemoryRouter>,
    );

    const note = await screen.findByText(/没有可用的云工作区/);
    expect(note.className).toContain("text-primary");
    expect(note.className).not.toContain("destructive");
  });

  it("shows scope validation as muted (form check, not needs-you)", () => {
    renderEditor(
      makeTask({
        templateConfig: {
          includeGlobal: false,
          folderIds: [],
          lookbackHours: 24,
        },
      }),
    );

    const note = screen.getByText(/请至少勾选/);
    expect(note.className).toContain("text-muted-foreground");
    expect(note.className).not.toContain("destructive");
    expect(note.className).not.toContain("text-primary");
  });

  it("shows a recoverable save failure as muted inline text", async () => {
    patch.mockRejectedValueOnce(
      new ApiError(500, JSON.stringify({ error: { message: "保存开小差" } })),
    );
    renderEditor(makeTask());
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    const err = await screen.findByText("保存开小差");
    expect(err.className).toContain("text-muted-foreground");
    expect(err.className).not.toContain("destructive");
  });
});
