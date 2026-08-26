import { ConversationChangesPanel } from "@/components/workspace/ConversationChangesPanel";
import { queryClient } from "@/lib/queryClient";
import { workspaceKeys } from "@/lib/queryKeys";
import type { WorkspaceInfo } from "@/services/workspaces";
import { useAutoSnapshotStore } from "@/stores/autoSnapshot";
import { useConversationStore } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import type { Message } from "@/stores/conversation/types";
import { useExecutionStore } from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import type { ProcessStep } from "@/types/events";
// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { useLocalTurnBaselineIds } = vi.hoisted(() => ({
  useLocalTurnBaselineIds: vi.fn((): ReadonlySet<string> => new Set()),
}));

const { useConversationWorkspace } = vi.hoisted(() => ({
  useConversationWorkspace: vi.fn((): WorkspaceInfo | null => null),
}));

const { hasLocalFiles } = vi.hoisted(() => ({
  hasLocalFiles: vi.fn(() => false),
}));

vi.mock("@/hooks/useLocalTurnBaselineIds", () => ({
  useLocalTurnBaselineIds,
}));

vi.mock("@/hooks/useGitRepoStatus", () => ({
  useGitRepoStatus: () => ({ status: null, refresh: vi.fn() }),
}));

vi.mock("@/components/workspace/WorkspaceModeControl", () => ({
  useWorkspaceModeState: () => null,
}));

vi.mock("@/hooks/useWorkspaces", () => ({ useConversationWorkspace }));

vi.mock("@/lib/capabilities", () => ({ hasLocalFiles }));

vi.mock("@/components/chat/TurnFileChangesReview", () => ({
  TurnFileChangesReview: ({
    messageId,
    artifacts,
    heading,
  }: {
    messageId?: string | null;
    artifacts: unknown[];
    heading?: string;
  }) => (
    <div data-testid={`review-${messageId}`}>
      {heading ? <span>{heading}</span> : null}
      artifacts:{artifacts.length}
    </div>
  ),
}));

function assistant(id: string, at: string, content = "ok"): Message {
  return {
    id,
    role: "assistant",
    content,
    createdAt: at,
    executionId: null,
    isStreaming: false,
  };
}

function setMessages(messages: Message[]): void {
  useConversationStore.setState({
    currentConversationId: "c1",
    byId: { c1: { ...EMPTY_RUNTIME, messages } },
  });
}

describe("ConversationChangesPanel P0c entry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useLocalTurnBaselineIds.mockReturnValue(new Set());
    useConversationWorkspace.mockReturnValue(null);
    hasLocalFiles.mockReturnValue(false);
    useAutoSnapshotStore.setState({ failedByConversation: {} });
    useExecutionStore.setState({ byId: {} });
    useSidePanelStore.setState({ changesFocusMessageId: null });
    setMessages([
      {
        id: "u1",
        role: "user",
        content: "hi",
        createdAt: "2026-08-10T10:00:00Z",
        executionId: null,
        isStreaming: false,
      },
      assistant(
        "a-baseline-only",
        "2026-08-10T10:00:00Z",
        "script deleted tree",
      ),
      assistant("a-no-baseline", "2026-08-10T12:00:00Z", "plain reply"),
    ]);
  });

  afterEach(cleanup);

  it("lists baseline-only turns without file_* artifacts", () => {
    useLocalTurnBaselineIds.mockReturnValue(new Set(["a-baseline-only"]));

    render(<ConversationChangesPanel />);

    expect(screen.getByTestId("review-a-baseline-only")).toBeTruthy();
    expect(screen.getByText("回合 1")).toBeTruthy();
    expect(screen.queryByTestId("review-a-no-baseline")).toBeNull();
    expect(screen.getByTestId("review-a-baseline-only").textContent).toContain(
      "artifacts:0",
    );
  });

  it("empty state has no 留版本 button", () => {
    render(<ConversationChangesPanel />);

    expect(screen.getByText("暂无改动")).toBeTruthy();
    expect(
      screen.getByText("本对话尚无 AI 文件改动，也没有可恢复的回合基线。"),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "留版本" })).toBeNull();
  });

  it("does not invalidate workspace list when the first artifacts appear", () => {
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const write: ProcessStep = {
      kind: "tool",
      id: "t-write",
      tool_name: "file_write",
      arguments: { path: "a.ts", content: "x" },
      result: null,
      status: "success",
    };
    setMessages([
      {
        ...assistant("a-write", "2026-08-10T10:00:00Z"),
        process: [write],
      },
    ]);

    render(<ConversationChangesPanel />);

    expect(screen.getByTestId("review-a-write")).toBeTruthy();
    expect(screen.getByTestId("review-a-write").textContent).toContain(
      "artifacts:1",
    );
    expect(spy).not.toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: workspaceKeys.list }),
    );
    spy.mockRestore();
  });
});

describe("ConversationChangesPanel auto-backup failure notice", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useConversationWorkspace.mockReturnValue(null);
    hasLocalFiles.mockReturnValue(false);
    useLocalTurnBaselineIds.mockReturnValue(new Set(["a-turn-1"]));
    useAutoSnapshotStore.setState({ failedByConversation: {} });
    useExecutionStore.setState({ byId: {} });
    useSidePanelStore.setState({ changesFocusMessageId: null });
    setMessages([assistant("a-turn-1", "2026-08-10T10:00:00Z")]);
  });

  afterEach(cleanup);

  const NOTICE =
    "最近一次自动备份失败。回合已正常完成，下次改文件的回合会再试。";

  it("stays quiet while auto-backup is healthy", async () => {
    render(<ConversationChangesPanel />);

    await waitFor(() => {
      expect(screen.getByTestId("changes-timeline")).toBeTruthy();
    });
    expect(screen.queryByText(NOTICE)).toBeNull();
  });

  it("surfaces the SSE-marked failure above the timeline", async () => {
    useAutoSnapshotStore.getState().markFailed("c1");

    render(<ConversationChangesPanel />);

    const notice = await screen.findByText(NOTICE);
    const timeline = screen.getByTestId("changes-timeline");
    expect(
      notice.compareDocumentPosition(timeline) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("shows it on the empty state too, then clears when a backup succeeds", async () => {
    useLocalTurnBaselineIds.mockReturnValue(new Set());
    useAutoSnapshotStore.getState().markFailed("c1");

    render(<ConversationChangesPanel />);

    expect(await screen.findByText(NOTICE)).toBeTruthy();
    expect(screen.getByText("暂无改动")).toBeTruthy();

    act(() => useAutoSnapshotStore.getState().clearFailed("c1"));
    expect(screen.queryByText(NOTICE)).toBeNull();
  });
});
