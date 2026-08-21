import {
  installAccountStateIngress,
  resetAccountStateIngressForTests,
} from "@/services/accountStateIngress";
import { ApiError, api } from "@/services/api";
import {
  clearActiveSidecarTurn,
  resetSidecarRoutingForTests,
  setActiveSidecarTurn,
} from "@/services/sidecarRouting";
import {
  cancelQueuedTurn,
  clearQueuedTurnLocally,
  steerQueuedTurn,
} from "@/services/turns/cancelQueuedTurn";
import { sendMidFlightMessage } from "@/services/turns/midFlight";
import { useConversationStore } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import { useQueuedTurnsStore } from "@/stores/queuedTurns";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api")>();
  return {
    ...actual,
    api: { ...actual.api, post: vi.fn() },
  };
});

vi.mock("@/services/turns/midFlight", () => ({
  sendMidFlightMessage: vi.fn(),
}));

let cloudCb: ((frame: unknown) => void) | null = null;

vi.mock("@/services/fulfillStream", () => ({
  onFulfillFrame: (cb: (frame: unknown) => void) => {
    cloudCb = cb;
    return () => {
      cloudCb = null;
    };
  },
}));

const post = vi.mocked(api.post);
const sendMidFlight = vi.mocked(sendMidFlightMessage);
const CID = "conv-cancel-q";

/** Happy path：排队期无用户泡，仅条。 */
function seedQueuedBarOnly(content = "queued") {
  useConversationStore.getState().switchConversation(CID);
  useQueuedTurnsStore.getState().upsert({
    queueId: "q1",
    conversationId: CID,
    content,
    position: 1,
    queueDepth: 1,
  });
}

/** 防御：出队插泡后仍挂 messageId 时取消可顺带删泡。 */
function seedQueuedWithBubble() {
  useConversationStore.getState().switchConversation(CID);
  useConversationStore.getState().addMessage(
    {
      id: "user-q",
      role: "user",
      content: "queued",
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
    content: "queued",
    position: 1,
    queueDepth: 1,
  });
}

const SNAPSHOT_ATTACHMENTS = [
  {
    name: "brief.txt",
    path: "attachments/brief.txt",
    text: "brief body",
    truncated: false,
    kind: "file" as const,
    workspace_path: "attachments/brief.txt",
  },
];
const SNAPSHOT_MENTIONS = [{ agent_id: "agent-research", role: "研究员" }];

beforeEach(() => {
  post.mockReset();
  sendMidFlight.mockReset();
  resetSidecarRoutingForTests();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useQueuedTurnsStore.setState({ byConversation: {} });
  resetAccountStateIngressForTests();
  cloudCb = null;
  installAccountStateIngress();
});

afterEach(() => {
  resetAccountStateIngressForTests();
  resetSidecarRoutingForTests();
  useQueuedTurnsStore.setState({ byConversation: {} });
  vi.unstubAllGlobals();
});

describe("clearQueuedTurnLocally", () => {
  it("无泡：只清条（幂等）", () => {
    seedQueuedBarOnly();
    expect(clearQueuedTurnLocally(CID, "q1")?.queueId).toBe("q1");
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
    expect(clearQueuedTurnLocally(CID, "q1")).toBeNull();
  });

  it("有 messageId：清条并删对应泡", () => {
    seedQueuedWithBubble();
    expect(clearQueuedTurnLocally(CID, "q1")?.messageId).toBe("user-q");
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
    expect(
      useConversationStore
        .getState()
        .byId[CID]?.messages.find((m) => m.id === "user-q"),
    ).toBeUndefined();
  });
});

describe("cancelQueuedTurn", () => {
  it("HTTP 成功 → 立刻本地清条（无泡）并返回 cancelled", async () => {
    seedQueuedBarOnly();
    post.mockResolvedValueOnce({});
    await expect(cancelQueuedTurn(CID, "q1")).resolves.toBe("cancelled");
    expect(post).toHaveBeenCalledWith(
      `/v1/conversations/${CID}/queued-turns/q1/cancel`,
      {},
    );
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
  });

  it("404（已不在队）→ 同样本地清该项并返回 already_gone", async () => {
    seedQueuedBarOnly();
    post.mockRejectedValueOnce(new ApiError(404, "{}"));
    await expect(cancelQueuedTurn(CID, "q1")).resolves.toBe("already_gone");
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
  });

  it("其它错误 → 抛出且不清 UI", async () => {
    seedQueuedBarOnly();
    post.mockRejectedValueOnce(new ApiError(500, "{}"));
    await expect(cancelQueuedTurn(CID, "q1")).rejects.toBeInstanceOf(ApiError);
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);
  });

  it("插话升队项亦可按 queue_id 取消", async () => {
    useConversationStore.getState().switchConversation(CID);
    useQueuedTurnsStore.getState().upsert({
      queueId: "q-ij",
      conversationId: CID,
      content: "来自插话",
      position: 1,
      queueDepth: 1,
      interjectionId: "ij-1",
    });
    post.mockResolvedValueOnce({});
    await expect(cancelQueuedTurn(CID, "q-ij")).resolves.toBe("cancelled");
    expect(post).toHaveBeenCalledWith(
      `/v1/conversations/${CID}/queued-turns/q-ij/cancel`,
      {},
    );
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
  });

  it("sidecar live 走 RPC，不 POST 云 cancel", async () => {
    seedQueuedBarOnly();
    setActiveSidecarTurn(CID, "root-1", "sub");
    const cancelRpc = vi.fn().mockResolvedValue({ status: "cancelled" });
    vi.stubGlobal("window", { sidecarApi: { cancelQueuedTurn: cancelRpc } });

    await expect(cancelQueuedTurn(CID, "q1")).resolves.toBe("cancelled");
    expect(cancelRpc).toHaveBeenCalledWith({
      rootId: "root-1",
      subpath: "sub",
      conversationId: CID,
      queueId: "q1",
    });
    expect(post).not.toHaveBeenCalled();
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
  });

  it("sidecar RPC not_found → already_gone，仍清条", async () => {
    seedQueuedBarOnly();
    setActiveSidecarTurn(CID, "root-1", "");
    const cancelRpc = vi.fn().mockResolvedValue({ status: "not_found" });
    vi.stubGlobal("window", { sidecarApi: { cancelQueuedTurn: cancelRpc } });

    await expect(cancelQueuedTurn(CID, "q1")).resolves.toBe("already_gone");
    expect(post).not.toHaveBeenCalled();
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
  });

  it("executionVia=sidecar 无 live 仍走 RPC（last target）", async () => {
    seedQueuedBarOnly();
    setActiveSidecarTurn(CID, "root-1", "");
    clearActiveSidecarTurn(CID);
    useConversationStore.setState((s) => ({
      byId: {
        ...s.byId,
        [CID]: {
          ...(s.byId[CID] ?? EMPTY_RUNTIME),
          executionVia: "sidecar",
        },
      },
    }));
    const cancelRpc = vi.fn().mockResolvedValue({ status: "cancelled" });
    vi.stubGlobal("window", { sidecarApi: { cancelQueuedTurn: cancelRpc } });

    await expect(cancelQueuedTurn(CID, "q1")).resolves.toBe("cancelled");
    expect(cancelRpc).toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it("无 sidecar live 且非本机队 → 仍走云 POST", async () => {
    seedQueuedBarOnly();
    const cancelRpc = vi.fn();
    vi.stubGlobal("window", { sidecarApi: { cancelQueuedTurn: cancelRpc } });
    post.mockResolvedValueOnce({});
    await expect(cancelQueuedTurn(CID, "q1")).resolves.toBe("cancelled");
    expect(cancelRpc).not.toHaveBeenCalled();
    expect(post).toHaveBeenCalled();
  });
});

describe("steerQueuedTurn", () => {
  it("取消成功后以 delivery=steer 重发同内容", async () => {
    seedQueuedBarOnly("please jump");
    post.mockResolvedValueOnce({});
    sendMidFlight.mockResolvedValueOnce({
      kind: "received",
      interjectionId: "ij1",
    });

    await steerQueuedTurn(CID, "q1");

    expect(post).toHaveBeenCalledWith(
      `/v1/conversations/${CID}/queued-turns/q1/cancel`,
      {},
    );
    expect(sendMidFlight).toHaveBeenCalledTimes(1);
    expect(sendMidFlight).toHaveBeenCalledWith(
      CID,
      "please jump",
      undefined,
      "steer",
      undefined,
    );
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
  });

  it("排队时带附件 + 点名 → 取消插队重发后两者都还在", async () => {
    useConversationStore.getState().switchConversation(CID);
    cloudCb?.({
      type: "turn_queue_snapshot",
      payload: {
        conversation_id: CID,
        items: [
          {
            queue_id: "q1",
            content: "请按附件看",
            position: 1,
            attachments: SNAPSHOT_ATTACHMENTS,
            agent_mentions: SNAPSHOT_MENTIONS,
          },
        ],
      },
    });
    post.mockResolvedValueOnce({});
    sendMidFlight.mockResolvedValueOnce({
      kind: "received",
      interjectionId: "ij1",
    });

    await steerQueuedTurn(CID, "q1");

    expect(sendMidFlight).toHaveBeenCalledTimes(1);
    expect(sendMidFlight).toHaveBeenCalledWith(
      CID,
      "请按附件看",
      [
        expect.objectContaining({
          name: "brief.txt",
          path: "attachments/brief.txt",
          text: "brief body",
          workspace_path: "attachments/brief.txt",
        }),
      ],
      "steer",
      SNAPSHOT_MENTIONS,
    );
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
  });

  it("跨重启：store 为空、仅账号快照时插队重发仍带附件与点名", async () => {
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
    useConversationStore.getState().switchConversation(CID);
    cloudCb?.({
      type: "turn_queue_account_snapshot",
      payload: {
        queues: [
          {
            conversation_id: CID,
            items: [
              {
                queue_id: "q-restart",
                content: "请按附件看",
                position: 1,
                attachments: SNAPSHOT_ATTACHMENTS,
                agent_mentions: SNAPSHOT_MENTIONS,
              },
            ],
          },
        ],
      },
    });
    post.mockResolvedValueOnce({});
    sendMidFlight.mockResolvedValueOnce({
      kind: "received",
      interjectionId: "ij-restart",
    });

    await steerQueuedTurn(CID, "q-restart");

    expect(sendMidFlight).toHaveBeenCalledTimes(1);
    expect(sendMidFlight).toHaveBeenCalledWith(
      CID,
      "请按附件看",
      [
        expect.objectContaining({
          name: "brief.txt",
          path: "attachments/brief.txt",
          workspace_path: "attachments/brief.txt",
        }),
      ],
      "steer",
      SNAPSHOT_MENTIONS,
    );
  });

  it("404（已出队/竞态）→ 只清条、不重发", async () => {
    seedQueuedBarOnly("already running");
    post.mockRejectedValueOnce(new ApiError(404, "{}"));

    await steerQueuedTurn(CID, "q1");

    expect(sendMidFlight).not.toHaveBeenCalled();
    expect(useQueuedTurnsStore.getState().list(CID)).toEqual([]);
  });

  it("取消失败 → 抛出、不重发、条仍在", async () => {
    seedQueuedBarOnly("keep me");
    post.mockRejectedValueOnce(new ApiError(500, "{}"));

    await expect(steerQueuedTurn(CID, "q1")).rejects.toBeInstanceOf(ApiError);
    expect(sendMidFlight).not.toHaveBeenCalled();
    expect(useQueuedTurnsStore.getState().list(CID)).toHaveLength(1);
  });

  it("本地已无该项 → no-op、不调 cancel/重发", async () => {
    await steerQueuedTurn(CID, "missing");
    expect(post).not.toHaveBeenCalled();
    expect(sendMidFlight).not.toHaveBeenCalled();
  });
});
