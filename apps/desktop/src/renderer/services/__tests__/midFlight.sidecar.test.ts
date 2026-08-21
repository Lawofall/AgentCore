import { notifyError } from "@/lib/toast";
import {
  clearActiveSidecarTurn,
  resetSidecarRoutingForTests,
  setActiveSidecarTurn,
} from "@/services/sidecarRouting";
import { sendMidFlightMessage } from "@/services/turns/midFlight";
import { useConversationStore } from "@/stores/conversation";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/toast", () => ({
  notifyInfo: vi.fn(),
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

const notifyErrorMock = vi.mocked(notifyError);
const CID = "conv-mf-sidecar";

beforeEach(() => {
  vi.clearAllMocks();
  resetSidecarRoutingForTests();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useQueuedTurnsStore.setState({ byConversation: {} });
  useConversationStore.getState().switchConversation(CID);
  useConversationStore.getState().setTurnPhase("streaming", CID);
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetSidecarRoutingForTests();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useQueuedTurnsStore.setState({ byConversation: {} });
});

describe("sendMidFlightMessage · sidecar live", () => {
  it("sidecar live 走 RPC queued，不打云 /messages", async () => {
    setActiveSidecarTurn(CID, "root-1", "conversations/x");
    const deliverMessage = vi.fn().mockResolvedValue({
      status: "queued",
      queueId: "q-side",
      position: 1,
      queueDepth: 1,
    });
    vi.stubGlobal("window", { sidecarApi: { deliverMessage } });

    const result = await sendMidFlightMessage(
      CID,
      "下一句",
      undefined,
      "queue",
    );

    expect(result).toEqual({
      kind: "queued",
      queueId: "q-side",
      position: 1,
      queueDepth: 1,
    });
    expect(deliverMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        rootId: "root-1",
        subpath: "conversations/x",
        conversationId: CID,
        content: "下一句",
        delivery: "queue",
        userMessageId: expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
        ),
        messageId: expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
        ),
        traceId: expect.stringMatching(/^[0-9a-f]{32}$/i),
      }),
    );
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    expect(useQueuedTurnsStore.getState().list(CID)[0]?.queueId).toBe("q-side");
    expect(useQueuedTurnsStore.getState().list(CID)[0]?.messageId).toEqual(
      deliverMessage.mock.calls[0]?.[0]?.userMessageId,
    );
  });

  it("sidecar live blocked 不打 /messages，不回落云", async () => {
    setActiveSidecarTurn(CID, "root-1", "");
    const deliverMessage = vi.fn().mockResolvedValue({
      status: "blocked",
      code: "pending_interactions_awaiting",
    });
    vi.stubGlobal("window", { sidecarApi: { deliverMessage } });

    await expect(
      sendMidFlightMessage(CID, "插一句", undefined, "steer"),
    ).resolves.toEqual({
      kind: "blocked",
      code: "pending_interactions_awaiting",
    });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("sidecar live received 落到现有 result.kind，不打 /messages", async () => {
    setActiveSidecarTurn(CID, "root-1", "");
    const deliverMessage = vi.fn().mockResolvedValue({
      status: "received",
      interjectionId: "ij-1",
    });
    vi.stubGlobal("window", { sidecarApi: { deliverMessage } });

    await expect(
      sendMidFlightMessage(CID, "插一句", undefined, "steer"),
    ).resolves.toEqual({ kind: "received", interjectionId: "ij-1" });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("sidecar live RPC 失败 → error，禁止回落云 POST", async () => {
    setActiveSidecarTurn(CID, "root-1", "");
    const deliverMessage = vi
      .fn()
      .mockRejectedValue(new Error("本地引擎未运行"));
    vi.stubGlobal("window", { sidecarApi: { deliverMessage } });

    await expect(
      sendMidFlightMessage(CID, "插一句", undefined, "steer"),
    ).resolves.toEqual({ kind: "error" });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    expect(notifyErrorMock).toHaveBeenCalled();
  });

  it("活回合已清、本机队未空 → 仍走 sidecar，不打云 /messages", async () => {
    setActiveSidecarTurn(CID, "root-1", "conversations/x");
    clearActiveSidecarTurn(CID);
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-hold",
      conversationId: CID,
      content: "等着",
      position: 1,
      queueDepth: 1,
    });
    const deliverMessage = vi.fn().mockResolvedValue({
      status: "queued",
      queueId: "q-next",
      position: 2,
      queueDepth: 2,
    });
    vi.stubGlobal("window", { sidecarApi: { deliverMessage } });

    const result = await sendMidFlightMessage(
      CID,
      "再排一条",
      undefined,
      "queue",
    );
    expect(result.kind).toBe("queued");
    expect(deliverMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        rootId: "root-1",
        subpath: "conversations/x",
        conversationId: CID,
        delivery: "queue",
      }),
    );
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("无 sidecar live 不走 RPC，仍 POST 云 /messages", async () => {
    const deliverMessage = vi.fn();
    vi.stubGlobal("window", { sidecarApi: { deliverMessage } });
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        `data: ${JSON.stringify({
          type: "user_interjection",
          timestamp: "t",
          payload: {
            interjection_id: "ij-cloud",
            status: "received",
          },
        })}\n\n`,
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );

    const result = await sendMidFlightMessage(
      CID,
      "插一句",
      undefined,
      "steer",
    );
    expect(result).toEqual({
      kind: "received",
      interjectionId: "ij-cloud",
    });
    expect(deliverMessage).not.toHaveBeenCalled();
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringMatching(/\/v1\/conversations\/.*\/messages$/),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
