// @vitest-environment jsdom
import { QueuedTurnsBar } from "@/components/chat/QueuedTurnsBar";
import { inlineToken } from "@/lib/inlineBody";
import { notifyError } from "@/lib/toast";
import { ApiError, api } from "@/services/api";
import { useConversationStore } from "@/stores/conversation";
import { execRuntime, useExecutionStore } from "@/stores/execution";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api")>();
  return {
    ...actual,
    api: { ...actual.api, post: vi.fn() },
  };
});

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyInfo: vi.fn(),
}));

const post = vi.mocked(api.post);
const CID = "conv-bar-q";

beforeEach(() => {
  post.mockReset();
  useQueuedTurnsStore.setState({ byConversation: {} });
});

afterEach(() => {
  cleanup();
  useQueuedTurnsStore.setState({ byConversation: {} });
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useExecutionStore.setState({ byId: {} });
});

function markTeamLive(messageId: string) {
  const base = execRuntime(useExecutionStore.getState(), null);
  useExecutionStore.setState({
    byId: {
      [messageId]: {
        ...base,
        status: "running",
        plan: {} as NonNullable<typeof base.plan>,
      },
    },
  });
}

describe("QueuedTurnsBar", () => {
  it("插话升队项标注来源且可取消", async () => {
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-ij",
      conversationId: CID,
      content: "协调升格的话",
      position: 1,
      queueDepth: 1,
      interjectionId: "ij-9",
    });
    post.mockResolvedValue({});

    render(<QueuedTurnsBar conversationId={CID} />);

    const row = screen.getByTestId("queued-turn-row");
    expect(row.getAttribute("data-from-interjection")).toBe("true");
    expect(row.textContent).toContain("来自你的插话");
    expect(row.textContent).toContain("协调升格的话");
    expect(row.textContent).not.toContain("\uFFFC");

    fireEvent.click(screen.getByTestId("queued-turn-cancel"));
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        `/v1/conversations/${CID}/queued-turns/q-ij/cancel`,
        {},
      );
    });
    await waitFor(() => {
      expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
    });
  });

  it("queued preview strips inline markers", () => {
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-mark",
      conversationId: CID,
      content: `协调${inlineToken("A", 0)}升格的话`,
      attachments: [
        {
          name: "brief.md",
          path: "brief.md",
          text: "",
          truncated: false,
          kind: "file",
        },
      ],
      position: 1,
      queueDepth: 1,
    });

    render(<QueuedTurnsBar conversationId={CID} />);
    const row = screen.getByTestId("queued-turn-row");
    expect(row.textContent).toContain("协调[文件 brief.md]升格的话");
    expect(row.textContent).not.toContain("\uFFFC");
  });

  it("单条也画排队条，且不显示序号", () => {
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().addMessage(
      {
        id: "user-q",
        role: "user",
        content: "是这样？",
        createdAt: new Date().toISOString(),
        executionId: null,
        isStreaming: false,
      },
      CID,
    );
    useQueuedTurnsStore.getState().upsert({
      queueId: "q1",
      conversationId: CID,
      messageId: "user-q",
      content: "是这样？",
      position: 1,
      queueDepth: 1,
    });
    render(<QueuedTurnsBar conversationId={CID} />);
    const row = screen.getByTestId("queued-turn-row");
    expect(row.textContent).toContain("排队中");
    expect(row.textContent).not.toContain("第 1/");
    expect(screen.queryByRole("button", { name: "软插队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "送给主管" })).toBeNull();
    expect(screen.queryByRole("button", { name: "停止并发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "停掉团队并发送" })).toBeNull();
  });

  it("团队还在：送给主管在排队条上，不出现停队发送", async () => {
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().addMessage(
      {
        id: "asst-live",
        role: "assistant",
        content: "",
        createdAt: new Date().toISOString(),
        executionId: null,
        isStreaming: true,
      },
      CID,
    );
    markTeamLive("asst-live");
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-live",
      conversationId: CID,
      content: "你我测试下",
      position: 1,
      queueDepth: 1,
    });
    post.mockResolvedValue({});

    render(<QueuedTurnsBar conversationId={CID} />);

    expect(screen.getByRole("button", { name: "送给主管" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "停掉团队并发送" })).toBeNull();
    expect(screen.queryByTestId("queued-turn-stop-send")).toBeNull();

    fireEvent.click(screen.getByTestId("queued-turn-to-captain"));
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        `/v1/conversations/${CID}/queued-turns/q-live/to-captain`,
        {},
      );
    });
    await waitFor(() => {
      expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
    });
  });

  it("团队已散：送给主管失败时这条仍留在排队条", async () => {
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().addMessage(
      {
        id: "asst-live",
        role: "assistant",
        content: "",
        createdAt: new Date().toISOString(),
        executionId: null,
        isStreaming: true,
      },
      CID,
    );
    markTeamLive("asst-live");
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-stay",
      conversationId: CID,
      content: "你我测试下",
      position: 1,
      queueDepth: 1,
    });
    post.mockRejectedValue(new ApiError(409, "{}"));

    render(<QueuedTurnsBar conversationId={CID} />);
    fireEvent.click(screen.getByTestId("queued-turn-to-captain"));
    await waitFor(() => {
      expect(notifyError).toHaveBeenCalledWith(
        "团队已经不在，这句话还在排队",
        "送给主管失败",
      );
    });
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);
  });

  it("两条时仍画排队条", () => {
    useQueuedTurnsStore.getState().upsert({
      queueId: "q1",
      conversationId: CID,
      messageId: "user-q",
      content: "一",
      position: 1,
      queueDepth: 2,
    });
    useQueuedTurnsStore.getState().upsert({
      queueId: "q2",
      conversationId: CID,
      content: "二",
      position: 2,
      queueDepth: 2,
    });
    render(<QueuedTurnsBar conversationId={CID} />);
    expect(screen.getByTestId("queued-turns-bar")).toBeTruthy();
    expect(screen.getAllByTestId("queued-turn-row")).toHaveLength(2);
  });

  it("404 取消亦清条（插话升队项）", async () => {
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-gone",
      conversationId: CID,
      content: "已出队",
      position: 1,
      queueDepth: 1,
      interjectionId: "ij-gone",
    });
    post.mockRejectedValue(new ApiError(404, "{}"));

    render(<QueuedTurnsBar conversationId={CID} />);
    fireEvent.click(screen.getByTestId("queued-turn-cancel"));
    await waitFor(() => {
      expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
    });
  });

  it("点排队行展开编辑，放弃后回到原句", () => {
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-edit",
      conversationId: CID,
      content: "先排队的话",
      position: 1,
      queueDepth: 1,
    });
    render(<QueuedTurnsBar conversationId={CID} />);
    fireEvent.click(screen.getByTestId("queued-turn-edit"));
    expect(screen.getByTestId("queued-turn-editor")).toBeTruthy();
    expect(screen.queryByTestId("queued-turn-to-captain")).toBeNull();
    expect(screen.queryByTestId("queued-turn-cancel")).toBeNull();
    fireEvent.click(screen.getByText("放弃"));
    expect(screen.queryByTestId("queued-turn-editor")).toBeNull();
    expect(screen.getByTestId("queued-turn-row").textContent).toContain(
      "先排队的话",
    );
  });
});
