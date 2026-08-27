// @vitest-environment jsdom
import { QueuedTurnsBar } from "@/components/chat/QueuedTurnsBar";
import { inlineToken } from "@/lib/inlineBody";
import { ApiError, api } from "@/services/api";
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

vi.mock("@/services/turns/midFlight", () => ({
  sendMidFlightMessage: vi.fn(),
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
});

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
});
