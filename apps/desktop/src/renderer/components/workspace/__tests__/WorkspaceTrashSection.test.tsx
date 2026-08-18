// @vitest-environment jsdom
/**
 * 「我的文件」的软删区 —— 云端工作区的可逆删除按 ws id 列出并一键还原。
 *
 * 与右坞同一块面板、同一套文案：保留天数取服务端的数，且必须继续说清「系统回收站里的
 * 删除不在此列」——这块面板从来只管工作区软删区那一条轨。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/workspaces", () => ({
  wsListTrash: vi.fn(),
  wsRestoreTrash: vi.fn(),
}));

vi.mock("@/services/workspace", () => ({
  listTrash: vi.fn(),
  restoreTrash: vi.fn(),
}));

import { listTrash, restoreTrash } from "@/services/workspace";
import { wsListTrash, wsRestoreTrash } from "@/services/workspaces";
import { TrashSection, WorkspaceTrashSection } from "../TrashSection";

afterEach(() => vi.restoreAllMocks());

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const entryA = {
  entryId: "t-a",
  originalPath: "old/a.md",
  name: "a.md",
  isDir: false,
  deletedAt: "2026-08-04T00:00:00Z",
};
const entryB = {
  entryId: "t-b",
  originalPath: "old/b.md",
  name: "b.md",
  isDir: false,
  deletedAt: "2026-08-04T00:00:00Z",
};

describe("文件页的软删区", () => {
  it("按 ws id 列出条目、照实说保留期，并能还原回原路径", async () => {
    vi.mocked(wsListTrash).mockResolvedValue({
      entries: [
        {
          entryId: "t1",
          originalPath: "报告/终稿.md",
          name: "终稿.md",
          isDir: false,
          deletedAt: "2026-08-04T00:00:00Z",
        },
      ],
      retentionDays: 30,
    });
    vi.mocked(wsRestoreTrash).mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <TooltipProvider>
        <WorkspaceTrashSection wsId="folder:f1" />
      </TooltipProvider>,
    );

    expect(await screen.findByText("终稿.md")).toBeTruthy();
    expect(wsListTrash).toHaveBeenCalledWith("folder:f1");
    expect(screen.getByText(/保留约 30 天/)).toBeTruthy();
    // 系统回收站是另一条轨，面板不得冒充能一键找回。
    expect(screen.getByText(/本地系统回收站删除不在此列/)).toBeTruthy();

    fireEvent.click(screen.getByLabelText("还原"));
    await waitFor(() =>
      expect(wsRestoreTrash).toHaveBeenCalledWith("folder:f1", "t1"),
    );
    // 还原后重新拉一次，列表不留幻影。
    await waitFor(() => expect(wsListTrash).toHaveBeenCalledTimes(2));
  });

  it("空的时候说清什么会进来", async () => {
    vi.mocked(wsListTrash).mockResolvedValue({
      entries: [],
      retentionDays: 30,
    });

    render(
      <TooltipProvider>
        <WorkspaceTrashSection wsId="folder:f1" />
      </TooltipProvider>,
    );

    expect(await screen.findByText("软删区为空")).toBeTruthy();
  });
});

describe("软删区切身份后旧请求晚回", () => {
  it("对话右坞切 conversationId：A 的晚到列表不得覆盖 B，还原打到 B", async () => {
    const hangA = deferred<{
      entries: typeof entryA[];
      retentionDays: number;
    }>();
    const hangB = deferred<{
      entries: typeof entryB[];
      retentionDays: number;
    }>();
    vi.mocked(listTrash).mockImplementation((id: string) => {
      if (id === "conv-a") return hangA.promise;
      return hangB.promise;
    });
    vi.mocked(restoreTrash).mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const { rerender } = render(
      <TooltipProvider>
        <TrashSection conversationId="conv-a" />
      </TooltipProvider>,
    );
    await waitFor(() => expect(listTrash).toHaveBeenCalledWith("conv-a"));

    rerender(
      <TooltipProvider>
        <TrashSection conversationId="conv-b" />
      </TooltipProvider>,
    );
    await waitFor(() => expect(listTrash).toHaveBeenCalledWith("conv-b"));

    await act(async () => {
      hangA.resolve({ entries: [entryA], retentionDays: 30 });
    });
    expect(screen.queryByText("a.md")).toBeNull();

    await act(async () => {
      hangB.resolve({ entries: [entryB], retentionDays: 30 });
    });
    expect(await screen.findByText("b.md")).toBeTruthy();
    expect(screen.queryByText("a.md")).toBeNull();

    fireEvent.click(screen.getByLabelText("还原"));
    await waitFor(() =>
      expect(restoreTrash).toHaveBeenCalledWith("conv-b", "t-b"),
    );
    expect(restoreTrash).not.toHaveBeenCalledWith("conv-a", "t-a");
    expect(restoreTrash).not.toHaveBeenCalledWith("conv-b", "t-a");
  });

  it("文件页切 wsId：A 的晚到列表不得覆盖 B，还原打到 B", async () => {
    const hangA = deferred<{
      entries: typeof entryA[];
      retentionDays: number;
    }>();
    const hangB = deferred<{
      entries: typeof entryB[];
      retentionDays: number;
    }>();
    vi.mocked(wsListTrash).mockImplementation((id: string) => {
      if (id === "folder:a") return hangA.promise;
      return hangB.promise;
    });
    vi.mocked(wsRestoreTrash).mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const { rerender } = render(
      <TooltipProvider>
        <WorkspaceTrashSection wsId="folder:a" />
      </TooltipProvider>,
    );
    await waitFor(() => expect(wsListTrash).toHaveBeenCalledWith("folder:a"));

    rerender(
      <TooltipProvider>
        <WorkspaceTrashSection wsId="folder:b" />
      </TooltipProvider>,
    );
    await waitFor(() => expect(wsListTrash).toHaveBeenCalledWith("folder:b"));

    await act(async () => {
      hangA.resolve({ entries: [entryA], retentionDays: 30 });
    });
    expect(screen.queryByText("a.md")).toBeNull();

    await act(async () => {
      hangB.resolve({ entries: [entryB], retentionDays: 30 });
    });
    expect(await screen.findByText("b.md")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("还原"));
    await waitFor(() =>
      expect(wsRestoreTrash).toHaveBeenCalledWith("folder:b", "t-b"),
    );
    expect(wsRestoreTrash).not.toHaveBeenCalledWith("folder:a", "t-a");
  });
});
