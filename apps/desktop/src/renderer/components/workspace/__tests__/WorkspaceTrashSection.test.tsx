// @vitest-environment jsdom
/**
 * 「我的文件」的软删区 —— 云端工作区的可逆删除按 ws id 列出并一键还原。
 *
 * 与右坞同一块面板。保留天数落在空态；顶栏不解释系统回收站（那是本机另一条轨）。
 * 文件页 tab 保活时切回可见会静默重拉，不挂人手刷新。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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
    expect(screen.queryByLabelText("刷新")).toBeNull();
    expect(screen.queryByText(/本地系统回收站删除不在此列/)).toBeNull();
    expect(screen.queryByText(/保留约 30 天/)).toBeNull();

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
    expect(screen.getByText(/约 30 天后自动清除/)).toBeTruthy();
  });

  it("tab 从隐藏切回可见时静默重拉，隐藏期间不拉", async () => {
    vi.mocked(wsListTrash).mockResolvedValue({
      entries: [],
      retentionDays: 30,
    });

    const { rerender } = render(
      <TooltipProvider>
        <WorkspaceTrashSection wsId="folder:f1" active />
      </TooltipProvider>,
    );
    await waitFor(() => expect(wsListTrash).toHaveBeenCalledTimes(1));

    rerender(
      <TooltipProvider>
        <WorkspaceTrashSection wsId="folder:f1" active={false} />
      </TooltipProvider>,
    );
    expect(wsListTrash).toHaveBeenCalledTimes(1);

    rerender(
      <TooltipProvider>
        <WorkspaceTrashSection wsId="folder:f1" active />
      </TooltipProvider>,
    );
    await waitFor(() => expect(wsListTrash).toHaveBeenCalledTimes(2));
  });
});

describe("软删区切身份后旧请求晚回", () => {
  it("对话右坞切 conversationId：A 的晚到列表不得覆盖 B，还原打到 B", async () => {
    const hangA = deferred<{
      entries: (typeof entryA)[];
      retentionDays: number;
    }>();
    const hangB = deferred<{
      entries: (typeof entryB)[];
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
      entries: (typeof entryA)[];
      retentionDays: number;
    }>();
    const hangB = deferred<{
      entries: (typeof entryB)[];
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
