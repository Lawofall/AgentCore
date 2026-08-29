// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/folders", () => ({ listFolders: vi.fn() }));
vi.mock("@/services/workflows", () => ({
  runWorkflow: vi.fn(),
  suggestWorkflowSlots: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({ notifySuccess: vi.fn() }));

import { ApiError } from "@/services/api";
import { type FolderMeta, listFolders } from "@/services/folders";
import { runWorkflow } from "@/services/workflows";
import { MemoryRouter } from "react-router-dom";
import { RunWorkflowDialog } from "../RunWorkflowDialog";

const folders = vi.mocked(listFolders);
const run = vi.mocked(runWorkflow);

function cloudFolder(id: string, name: string): FolderMeta {
  return { id, name, mode: "cloud", localRootId: null, localSubpath: null };
}

function renderDialog() {
  render(
    <MemoryRouter>
      <RunWorkflowDialog
        open
        workflowId="wf-1"
        workflowName="周报流水线"
        onClose={() => {}}
      />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  folders.mockReset();
  run.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("RunWorkflowDialog workspace list", () => {
  it("reports a failed request instead of claiming there are no workspaces", async () => {
    folders.mockRejectedValue(
      new ApiError(
        500,
        JSON.stringify({ error: { message: "工作区服务开小差" } }),
      ),
    );
    renderDialog();

    const err = await screen.findByText("工作区服务开小差");
    expect(err.className).toContain("text-muted-foreground");
    expect(err.className).not.toContain("destructive");
    expect(screen.queryByText(/还没有可用的文件夹/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "开跑" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("recovers through 重试 once the request succeeds", async () => {
    folders.mockRejectedValueOnce(new Error("boom"));
    folders.mockResolvedValueOnce([cloudFolder("f1", "工作")]);
    renderDialog();

    fireEvent.click(await screen.findByRole("button", { name: "重试" }));

    expect(await screen.findByRole("option", { name: "工作" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "开跑" }).hasAttribute("disabled"),
    ).toBe(false);
  });

  it("shows a recoverable run failure as muted inline text", async () => {
    folders.mockResolvedValue([cloudFolder("f1", "工作")]);
    run.mockRejectedValue(
      new ApiError(500, JSON.stringify({ error: { message: "开跑开小差" } })),
    );
    renderDialog();

    await screen.findByRole("option", { name: "工作" });
    fireEvent.click(screen.getByRole("button", { name: "开跑" }));

    const err = await screen.findByText("开跑开小差");
    expect(err.className).toContain("text-muted-foreground");
    expect(err.className).not.toContain("destructive");
  });

  it("only says the account has none when the request actually returned none", async () => {
    folders.mockResolvedValue([]);
    renderDialog();

    expect(await screen.findByText(/还没有可用的文件夹/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });
});
