import { TurnFileChangesReview } from "@/components/chat/TurnFileChangesReview";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { WorkspaceInfo } from "@/services/workspaces";
// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type Mock, beforeEach, describe, expect, it, vi } from "vitest";

const {
  getTurnFilesDiff,
  getLocalTurnFilesDiff,
  restoreLocalTurnBaseline,
  restoreSnapshot,
  downloadWorkspaceFile,
  notifySuccess,
  useConversationWorkspace,
} = vi.hoisted(() => ({
  getTurnFilesDiff: vi.fn(),
  getLocalTurnFilesDiff: vi.fn(),
  restoreLocalTurnBaseline: vi.fn(),
  restoreSnapshot: vi.fn(),
  downloadWorkspaceFile: vi.fn(),
  notifySuccess: vi.fn(),
  useConversationWorkspace: vi.fn((): WorkspaceInfo | null => null) as Mock<
    () => WorkspaceInfo | null
  >,
}));

vi.mock("@/services/turnFilesDiff", () => ({
  getTurnFilesDiff,
  getLocalTurnFilesDiff,
  restoreLocalTurnBaseline,
}));
vi.mock("@/services/workspace", () => ({
  restoreSnapshot,
  downloadWorkspaceFile,
}));
vi.mock("@/lib/toast", () => ({
  notifySuccess,
  notifyActionError: vi.fn(),
}));
vi.mock("@/hooks/useWorkspaces", () => ({
  useConversationWorkspace,
}));

describe("TurnFileChangesReview change labels", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useConversationWorkspace.mockReturnValue(null);
  });

  it("true diff labels by baseline presence: 新建/更新/删除 (never 写入/编辑)", async () => {
    getTurnFilesDiff.mockResolvedValue({
      messageId: "m1",
      baselineSnapshotId: "snap-1",
      available: true,
      changes: [
        {
          path: "new.ts",
          changeType: "added",
          baseSha: null,
          resultSha: "r1",
          isBinary: false,
          content: "hello",
          sizeBytes: 5,
          baseContent: null,
        },
        {
          path: "old.ts",
          changeType: "modified",
          baseSha: "b",
          resultSha: "r2",
          isBinary: false,
          content: "new",
          sizeBytes: 3,
          baseContent: "old",
        },
        {
          path: "gone.ts",
          changeType: "deleted",
          baseSha: "b",
          resultSha: null,
          isBinary: false,
          content: null,
          sizeBytes: 0,
          baseContent: "x",
        },
      ],
      total: 3,
      added: 1,
      modified: 1,
      deleted: 1,
    });

    render(
      <TooltipProvider>
        <TurnFileChangesReview
          conversationId="c1"
          messageId="m1"
          artifacts={[]}
        />
      </TooltipProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("新建")).toBeTruthy();
      expect(screen.getByText("更新")).toBeTruthy();
      expect(screen.getByText("删除")).toBeTruthy();
    });
    expect(screen.queryByText("写入")).toBeNull();
    expect(screen.queryByText("编辑")).toBeNull();
    expect(screen.queryByText("新增")).toBeNull();
    expect(screen.queryByText("修改")).toBeNull();
    // 单层 chrome：行数在折叠头，不在展开体内再标路径/模式
    expect(screen.getByText("1 行")).toBeTruthy();
    expect(screen.getAllByText("+1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("-1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("old.ts")).toHaveLength(1);
    expect(screen.getAllByText("new.ts")).toHaveLength(1);
  });

  it("tool-arg fallback labels write/edit as 更新, never 写入/编辑", async () => {
    getTurnFilesDiff.mockResolvedValue({
      messageId: "m1",
      baselineSnapshotId: null,
      available: false,
      changes: [],
      total: 0,
      added: 0,
      modified: 0,
      deleted: 0,
    });

    render(
      <TooltipProvider>
        <TurnFileChangesReview
          conversationId="c1"
          messageId="m1"
          artifacts={[
            {
              path: "a.ts",
              name: "a.ts",
              op: "write",
              change: {
                kind: "write",
                content: "body",
                mode: "overwrite",
              },
            },
            {
              path: "b.ts",
              name: "b.ts",
              op: "edit",
              change: { kind: "edit", oldText: "a", newText: "b" },
            },
            {
              path: "c.ts",
              name: "c.ts",
              op: "delete",
              change: { kind: "delete" },
            },
          ]}
        />
      </TooltipProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("a.ts")).toBeTruthy();
    });
    // Row labels: write+edit → 更新; delete → 删除
    expect(screen.getAllByText("更新").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("删除")).toBeTruthy();
    expect(screen.queryByText("写入")).toBeNull();
    expect(screen.queryByText("编辑")).toBeNull();
    // 单层 chrome：write 模式行数在折叠头；路径不重复
    expect(screen.getByText("1 行")).toBeTruthy();
    expect(screen.getAllByText("a.ts")).toHaveLength(1);
    expect(screen.getAllByText("b.ts")).toHaveLength(1);
  });
});

describe("TurnFileChangesReview A2′ rollback", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useConversationWorkspace.mockReturnValue(null);
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
  });

  it("shows rollback when true diff has baseline, and restores on confirm", async () => {
    getTurnFilesDiff.mockResolvedValue({
      messageId: "m1",
      baselineSnapshotId: "snap-1",
      available: true,
      changes: [
        {
          path: "a.ts",
          changeType: "modified",
          baseSha: "b",
          resultSha: "r",
          isBinary: false,
          content: "new",
          sizeBytes: 3,
          baseContent: "old",
        },
      ],
      total: 1,
      added: 0,
      modified: 1,
      deleted: 0,
    });
    restoreSnapshot.mockResolvedValue(undefined);

    render(
      <TooltipProvider>
        <TurnFileChangesReview
          conversationId="c1"
          messageId="m1"
          artifacts={[
            {
              path: "a.ts",
              name: "a.ts",
              op: "edit",
              change: { kind: "edit", oldText: "old", newText: "new" },
            },
          ]}
        />
      </TooltipProvider>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("恢复到本回合开始")).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("恢复到本回合开始"));
    await waitFor(() => {
      expect(restoreSnapshot).toHaveBeenCalledWith("c1", "snap-1");
    });
    expect(notifySuccess).not.toHaveBeenCalled();
    expect(getLocalTurnFilesDiff).not.toHaveBeenCalled();
    expect(restoreLocalTurnBaseline).not.toHaveBeenCalled();
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining("overlay"),
    );
  });

  it("hides rollback when falling back to tool-arg preview", async () => {
    getTurnFilesDiff.mockResolvedValue({
      messageId: "m1",
      baselineSnapshotId: null,
      available: false,
      changes: [],
      total: 0,
      added: 0,
      modified: 0,
      deleted: 0,
    });

    render(
      <TooltipProvider>
        <TurnFileChangesReview
          conversationId="c1"
          messageId="m1"
          artifacts={[
            {
              path: "a.ts",
              name: "a.ts",
              op: "edit",
              change: { kind: "edit", oldText: "a", newText: "b" },
            },
          ]}
        />
      </TooltipProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("a.ts")).toBeTruthy();
    });
    expect(screen.queryByLabelText("恢复到本回合开始")).toBeNull();
  });

  it("P0c: empty file_* artifacts still shows restore when baseline available", async () => {
    getTurnFilesDiff.mockResolvedValue({
      messageId: "m1",
      baselineSnapshotId: "snap-1",
      available: true,
      changes: [
        {
          path: "gone.ts",
          changeType: "deleted",
          baseSha: "b",
          resultSha: null,
          isBinary: false,
          content: null,
          sizeBytes: 0,
          baseContent: "x",
        },
      ],
      total: 1,
      added: 0,
      modified: 0,
      deleted: 1,
    });

    render(
      <TooltipProvider>
        <TurnFileChangesReview
          conversationId="c1"
          messageId="m1"
          artifacts={[]}
        />
      </TooltipProvider>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("恢复到本回合开始")).toBeTruthy();
    });
    expect(screen.getByText("删除")).toBeTruthy();
  });

  it("true diff with zero files still explains the empty body", async () => {
    getTurnFilesDiff.mockResolvedValue({
      messageId: "m1",
      baselineSnapshotId: "snap-1",
      available: true,
      changes: [],
      total: 0,
      added: 0,
      modified: 0,
      deleted: 0,
    });

    render(
      <TooltipProvider>
        <TurnFileChangesReview
          variant="panel"
          heading="回合 1"
          conversationId="c1"
          messageId="m1"
          artifacts={[]}
        />
      </TooltipProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("相对基线无文件差异")).toBeTruthy();
    });
    expect(screen.getByLabelText("恢复到本回合开始")).toBeTruthy();
    expect(screen.queryByText("暂无改动")).toBeNull();
  });

  it("uses sidecar local diff/restore when workspace location is local", async () => {
    useConversationWorkspace.mockReturnValue({
      wsId: "conv:c1",
      name: "c1",
      location: "local",
      rootId: "root-1",
      subpath: "conversations/c1",
      hasFiles: true,
    });
    getLocalTurnFilesDiff.mockResolvedValue({
      messageId: "m1",
      baselineSnapshotId: "m1",
      available: true,
      changes: [],
      total: 0,
      added: 0,
      modified: 0,
      deleted: 0,
    });
    restoreLocalTurnBaseline.mockResolvedValue(undefined);

    render(
      <TooltipProvider>
        <TurnFileChangesReview
          conversationId="c1"
          messageId="m1"
          artifacts={[
            {
              path: "a.ts",
              name: "a.ts",
              op: "edit",
              change: { kind: "edit", oldText: "a", newText: "b" },
            },
          ]}
        />
      </TooltipProvider>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("恢复到本回合开始")).toBeTruthy();
    });
    expect(getLocalTurnFilesDiff).toHaveBeenCalledWith(
      { rootId: "root-1", subpath: "conversations/c1" },
      "m1",
    );
    expect(getTurnFilesDiff).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText("恢复到本回合开始"));
    await waitFor(() => {
      expect(restoreLocalTurnBaseline).toHaveBeenCalledWith(
        { rootId: "root-1", subpath: "conversations/c1" },
        "m1",
      );
    });
    expect(restoreSnapshot).not.toHaveBeenCalled();
  });

  it("panel chrome is one row: heading, counts, restore, time; no file-count", async () => {
    getTurnFilesDiff.mockResolvedValue({
      messageId: "m1",
      baselineSnapshotId: "snap-1",
      available: true,
      changes: [
        {
          path: "a.ts",
          changeType: "added",
          baseSha: null,
          resultSha: "r",
          isBinary: false,
          content: "x",
          sizeBytes: 1,
          baseContent: null,
        },
        {
          path: "b.ts",
          changeType: "added",
          baseSha: null,
          resultSha: "r2",
          isBinary: false,
          content: "y",
          sizeBytes: 1,
          baseContent: null,
        },
      ],
      total: 2,
      added: 2,
      modified: 0,
      deleted: 0,
    });

    render(
      <TooltipProvider>
        <TurnFileChangesReview
          variant="panel"
          heading="回合 2"
          headingTime="01:12"
          conversationId="c1"
          messageId="m1"
          artifacts={[
            {
              path: "a.ts",
              name: "a.ts",
              op: "write",
              change: { kind: "write", content: "x", mode: "overwrite" },
            },
          ]}
        />
      </TooltipProvider>,
    );

    const restore = await screen.findByLabelText("恢复到本回合开始");
    expect(screen.getByText("回合 2")).toBeTruthy();
    expect(screen.getByText("+2")).toBeTruthy();
    expect(screen.getByText("01:12")).toBeTruthy();
    expect(screen.queryByText(/个文件/)).toBeNull();
    expect(restore.textContent).toContain("恢复");
    expect(restore.textContent).not.toContain("到本回合开始");
    expect(screen.queryByText("正在读取改动…")).toBeNull();
  });

  it("binary change offers direct download instead of workspace-only dead end", async () => {
    downloadWorkspaceFile.mockResolvedValue(undefined);
    getTurnFilesDiff.mockResolvedValue({
      messageId: "m1",
      baselineSnapshotId: "snap-1",
      available: true,
      changes: [
        {
          path: "独立站整改.zip",
          changeType: "added",
          baseSha: null,
          resultSha: "r1",
          isBinary: true,
          content: null,
          sizeBytes: 2048,
          baseContent: null,
        },
      ],
      total: 1,
      added: 1,
      modified: 0,
      deleted: 0,
    });

    render(
      <TooltipProvider>
        <TurnFileChangesReview
          conversationId="c1"
          messageId="m1"
          artifacts={[]}
        />
      </TooltipProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("独立站整改.zip")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("独立站整改.zip"));
    expect(screen.getByText(/可直接下载到本机/)).toBeTruthy();
    expect(screen.queryByText(/请在工作区打开/)).toBeNull();

    fireEvent.click(screen.getByLabelText("下载 独立站整改.zip"));
    await waitFor(() => {
      expect(downloadWorkspaceFile).toHaveBeenCalledWith(
        "c1",
        "独立站整改.zip",
        "独立站整改.zip",
      );
    });
  });
});
