// @vitest-environment jsdom
/**
 * 同组件切 toolbox/workflows/:workflowId 时，在途的旧 getWorkflow 不得把 A
 * 的定义画到 B；保存必须确认写回 id 与已加载来源 id 一致，否则会 PATCH 错对象。
 */
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import type { WorkflowDefinition } from "@/services/workflowDefinition";
import type { UserWorkflow } from "@/services/workflows";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
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
  WorkflowCanvas: () => <div data-testid="canvas" />,
}));

import { getWorkflow, patchWorkflow } from "@/services/workflows";
import { WorkflowEditorPage } from "../WorkflowEditorPage";

const load = vi.mocked(getWorkflow);
const save = vi.mocked(patchWorkflow);

function definition(task: string): WorkflowDefinition {
  return {
    nodes: [{ id: `${task}-node`, kind: "agent_step", role: "研究员", task }],
    edges: [],
  };
}

function workflow(id: string, name: string, task: string): UserWorkflow {
  return {
    id,
    name,
    description: null,
    definition: definition(task),
    source: null,
    version: 2,
    createdAt: "2026-08-01T00:00:00Z",
    updatedAt: "2026-08-01T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function Harness() {
  const navigate = useNavigate();
  return (
    <>
      <button
        type="button"
        data-testid="to-b"
        onClick={() => navigate(APP_PATHS.toolbox.workflows.edit("wf-b"))}
      >
        to-b
      </button>
      <WorkflowEditorPage />
    </>
  );
}

function renderAt(startId: string) {
  render(
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.workflows.edit(startId)]}>
      <Routes>
        <Route
          path={APP_PATHS.toolbox.workflows.edit(":workflowId")}
          element={<Harness />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  load.mockReset();
  save.mockReset();
  save.mockImplementation(async (id, patch) => ({
    ...workflow(id, patch.name ?? "saved", "saved"),
    definition: patch.definition ?? definition("saved"),
  }));
});

afterEach(() => {
  cleanup();
});

describe("WorkflowEditorPage · 切 id 旧请求晚回", () => {
  it("慢请求晚回不得把 A 的名称/定义落到 B 的 URL 上", async () => {
    const pendingA = deferred<UserWorkflow>();
    const pendingB = deferred<UserWorkflow>();
    load.mockImplementation((id: string) => {
      if (id === "wf-a") return pendingA.promise;
      if (id === "wf-b") return pendingB.promise;
      return Promise.reject(new Error(`unexpected workflow ${id}`));
    });

    renderAt("wf-a");
    expect(load).toHaveBeenCalledWith("wf-a");

    fireEvent.click(screen.getByTestId("to-b"));
    expect(load).toHaveBeenCalledWith("wf-b");

    await act(async () => {
      pendingA.resolve(workflow("wf-a", "工作流A", "A任务"));
    });
    expect(screen.queryByDisplayValue("工作流A")).toBeNull();
    expect(await screen.findByText("加载中…")).toBeTruthy();

    await act(async () => {
      pendingB.resolve(workflow("wf-b", "工作流B", "B任务"));
    });
    expect(await screen.findByDisplayValue("工作流B")).toBeTruthy();
    expect(screen.queryByDisplayValue("工作流A")).toBeNull();
    expect(screen.getByTestId("canvas")).toBeTruthy();
  });

  it("切到 B 后保存 PATCH 的是 B 的 id 与 B 的定义，不是 A", async () => {
    const pendingB = deferred<UserWorkflow>();
    load.mockImplementation((id: string) => {
      if (id === "wf-a") {
        return Promise.resolve(workflow("wf-a", "工作流A", "A任务"));
      }
      if (id === "wf-b") return pendingB.promise;
      return Promise.reject(new Error(`unexpected workflow ${id}`));
    });

    renderAt("wf-a");
    expect(await screen.findByDisplayValue("工作流A")).toBeTruthy();

    fireEvent.click(screen.getByTestId("to-b"));
    expect(await screen.findByText("加载中…")).toBeTruthy();

    await act(async () => {
      pendingB.resolve(workflow("wf-b", "工作流B", "B任务"));
    });
    expect(await screen.findByDisplayValue("工作流B")).toBeTruthy();
    expect(screen.queryByDisplayValue("工作流A")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save).toHaveBeenCalledWith(
      "wf-b",
      expect.objectContaining({
        name: "工作流B",
        definition: definition("B任务"),
      }),
    );
    expect(save.mock.calls[0]?.[0]).not.toBe("wf-a");
  });

  it("B 已落地后再收到 A 的慢响应：保存仍 PATCH B", async () => {
    const pendingA = deferred<UserWorkflow>();
    const pendingB = deferred<UserWorkflow>();
    load.mockImplementation((id: string) => {
      if (id === "wf-a") return pendingA.promise;
      if (id === "wf-b") return pendingB.promise;
      return Promise.reject(new Error(`unexpected workflow ${id}`));
    });

    renderAt("wf-a");
    fireEvent.click(screen.getByTestId("to-b"));

    await act(async () => {
      pendingB.resolve(workflow("wf-b", "工作流B", "B任务"));
    });
    expect(await screen.findByDisplayValue("工作流B")).toBeTruthy();

    await act(async () => {
      pendingA.resolve(workflow("wf-a", "工作流A", "A任务"));
    });
    expect(screen.getByDisplayValue("工作流B")).toBeTruthy();
    expect(screen.queryByDisplayValue("工作流A")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        "wf-b",
        expect.objectContaining({
          name: "工作流B",
          definition: definition("B任务"),
        }),
      ),
    );
  });
});
