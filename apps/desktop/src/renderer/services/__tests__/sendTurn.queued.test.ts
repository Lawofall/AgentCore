import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// sendTurn × 发送即有流：POST 恒 SSE；排队态由 turn_queued → QueuedTurnsBar，
// 同连接续流——不再有 SendOutcome.queued / watchQueuedTurn。
vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(() => []),
  bumpConversationCache: vi.fn(),
  restoreConversationCache: vi.fn(),
  syncConversationListPreview: vi.fn(),
}));
vi.mock("@/services/sidecarRouting", () => ({
  resolveSidecarRoot: vi.fn(() => Promise.resolve(null)),
  resolveConversationLocalTarget: vi.fn(() => Promise.resolve(null)),
  isSidecarEnabled: vi.fn(() => true),
}));
vi.mock("@/lib/capabilities", () => ({
  hasLocalEngine: vi.fn(() => true),
}));
vi.mock("@/lib/log", () => ({
  logEvent: vi.fn(),
}));
vi.mock("@/services/sidecarHealth", () => ({
  probeSidecar: vi.fn(),
  markSidecarUnhealthy: vi.fn(),
  clearSidecarHealth: vi.fn(),
}));
vi.mock("@/services/streamConversation", () => ({
  attachConversation: vi.fn(),
  streamConversation: vi.fn(),
  regenerateConversation: vi.fn(),
  resumeConversation: vi.fn(),
}));
vi.mock("@/services/streamConversationViaSidecar", () => ({
  streamConversationViaSidecar: vi.fn(),
  resumeConversationViaSidecar: vi.fn(),
}));
vi.mock("@/services/messages", () => ({ loadLatestWindow: vi.fn() }));
vi.mock("@/lib/toast", () => ({
  notifyInfo: vi.fn(),
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));
vi.mock("@/services/turns/recovery", () => ({
  rejoinLiveTurn: vi.fn(),
  cancelRejoinLiveTurn: vi.fn(),
  attachOnOpen: vi.fn(),
  markGhostInterrupted: vi.fn(),
  settleCloudRunningAssistant: vi.fn(),
  settleOrphanEmptyAssistants: vi.fn(),
}));

import { StreamError } from "@/lib/errors";
import { notifyInfo } from "@/lib/toast";
import { probeSidecar } from "@/services/sidecarHealth";
import { resolveSidecarRoot } from "@/services/sidecarRouting";
import { streamConversation } from "@/services/streamConversation";
import { streamConversationViaSidecar } from "@/services/streamConversationViaSidecar";
import { rejoinLiveTurn } from "@/services/turns/recovery";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { sendTurn } from "../turns/stream";

const streamMock = vi.mocked(streamConversation);
const sidecarStreamMock = vi.mocked(streamConversationViaSidecar);
const resolveRootMock = vi.mocked(resolveSidecarRoot);
const probeMock = vi.mocked(probeSidecar);
const rejoinMock = vi.mocked(rejoinLiveTurn);
const notifyInfoMock = vi.mocked(notifyInfo);

const CID = "conv-send-queued";

function spec() {
  return {
    conversationId: CID,
    content: "第二问",
    attachments: [],
    optimisticUserId: "opt-u2",
  };
}

function seedOptimisticUser(): void {
  useConversationStore.getState().addMessage(
    {
      id: "opt-u2",
      role: "user",
      content: "第二问",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    },
    CID,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resolveRootMock.mockResolvedValue(null);
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  seedOptimisticUser();
});

afterEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

describe("sendTurn — 发送即有流（无 202 / 无守望）", () => {
  it("streamConversation resolve → 正常收口，不 toast、不 rejoin", async () => {
    streamMock.mockResolvedValue(undefined);

    await sendTurn(spec());

    expect(streamMock).toHaveBeenCalledTimes(1);
    expect(rejoinMock).not.toHaveBeenCalled();
    expect(notifyInfoMock).not.toHaveBeenCalled();
    expect(getRuntime(CID).error).toBeNull();
  });

  it("流式路径打开助手占位（排队等待与空闲开跑共用）", async () => {
    streamMock.mockResolvedValue(undefined);

    await sendTurn(spec());

    const assistants = getRuntime(CID).messages.filter(
      (m) => m.role === "assistant",
    );
    expect(assistants.length).toBeGreaterThanOrEqual(1);
  });

  it("开跑前 402：撤乐观用户泡与空助手泡，phase 回 idle", async () => {
    streamMock.mockRejectedValue(
      new StreamError("http", 402, { code: "LLM_KEY_REQUIRED" }),
    );

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    const rt = getRuntime(CID);
    expect(rt.messages.some((m) => m.id === "opt-u2")).toBe(false);
    expect(rt.messages.some((m) => m.role === "assistant")).toBe(false);
    expect(rt.isGenerating).toBe(false);
    expect(rt.turnPhase).toBe("idle");
    expect(rt.error).toBeTruthy();
  });

  it("开跑前 429 额度：同样回滚，不留伪回合", async () => {
    streamMock.mockRejectedValue(
      new StreamError("http", 429, { code: "QUOTA_EXCEEDED" }),
    );

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(getRuntime(CID).messages).toHaveLength(0);
  });

  it("传输失败未落库：不回滚（不是 A 类）", async () => {
    streamMock.mockRejectedValue(new StreamError("network"));

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(false);
    expect(getRuntime(CID).messages.some((m) => m.id === "opt-u2")).toBe(true);
    expect(getRuntime(CID).messages.some((m) => m.role === "assistant")).toBe(
      true,
    );
  });
});

function persistEmptyAssistantFailure(opts?: {
  content?: string;
  withTool?: boolean;
  code?: string;
  /** Cloud Class B: swap optimistic id (turn_saved). Sidecar keeps the client id. */
  reconcile?: boolean;
}): void {
  const store = useConversationStore.getState();
  if (opts?.reconcile !== false) {
    store.reconcileLastTurn("u-server", CID);
  }
  if (opts?.content) {
    store.appendToLastMessage(opts.content, CID);
  }
  if (opts?.withTool) {
    store.addProcessTool(
      {
        tool_call_id: "t1",
        tool_name: "web_search",
        arguments: {},
      },
      CID,
    );
  }
  store.attachErrorToLastMessage(
    {
      code: opts?.code ?? "LLM_RATE_LIMIT",
      message: "上游限流，本回合无法继续。",
    },
    CID,
  );
  store.finalizeLastMessage(CID);
  store.setTurnPhase("failed", CID);
}

function reportCommitted(opts: {
  turnCommit?: { committed: boolean };
}): void {
  if (opts.turnCommit) opts.turnCommit.committed = true;
}

describe("sendTurn — Class B 零产出回滚（流 resolve）", () => {
  it("流 resolve + 空助手 LLM_RATE_LIMIT → 回滚 idle", async () => {
    streamMock.mockImplementation(async (opts) => {
      reportCommitted(opts);
      persistEmptyAssistantFailure();
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(result.supportPack?.conversationId).toBe(CID);
    expect(result.supportPack?.userMessageId).toBe("u-server");
    expect(result.supportPack?.errorCode).toBe("LLM_RATE_LIMIT");
    const rt = getRuntime(CID);
    expect(rt.messages).toHaveLength(0);
    expect(rt.isGenerating).toBe(false);
    expect(rt.turnPhase).toBe("idle");
    expect(rt.error).toBeTruthy();
  });

  it("已换 id 但传输未报告提交 → 不按 Class B 回滚", async () => {
    streamMock.mockImplementation(async () => {
      persistEmptyAssistantFailure();
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(false);
    expect(result.supportPack).toBeUndefined();
    expect(getRuntime(CID).messages.some((m) => m.role === "user")).toBe(true);
    expect(getRuntime(CID).turnPhase).toBe("failed");
  });

  it("有正文不滚", async () => {
    streamMock.mockImplementation(async (opts) => {
      reportCommitted(opts);
      persistEmptyAssistantFailure({ content: "半句" });
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(false);
    const rt = getRuntime(CID);
    expect(rt.messages.some((m) => m.role === "user")).toBe(true);
    expect(rt.messages.some((m) => m.role === "assistant")).toBe(true);
    expect(rt.turnPhase).toBe("failed");
  });

  it("有工具不滚", async () => {
    streamMock.mockImplementation(async (opts) => {
      reportCommitted(opts);
      persistEmptyAssistantFailure({ withTool: true });
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(false);
    expect(getRuntime(CID).messages.some((m) => m.role === "user")).toBe(true);
    expect(getRuntime(CID).turnPhase).toBe("failed");
  });

  it("catch 已 persist + 空助手 LLM_RATE_LIMIT → 同样回滚 idle", async () => {
    streamMock.mockImplementation(async (opts) => {
      reportCommitted(opts);
      persistEmptyAssistantFailure();
      throw new StreamError("http", 429, {
        code: "LLM_RATE_LIMIT",
        serverMessage: "上游限流，本回合无法继续。",
      });
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(result.supportPack?.userMessageId).toBe("u-server");
    const rt = getRuntime(CID);
    expect(rt.messages).toHaveLength(0);
    expect(rt.turnPhase).toBe("idle");
    expect(rt.error).toBeTruthy();
  });
});

describe("sendTurn — Class B 零产出回滚（sidecar）", () => {
  beforeEach(() => {
    resolveRootMock.mockResolvedValue({ rootId: "r1", subpath: "" });
    probeMock.mockResolvedValue({
      healthy: true,
      probed: true,
      detail: null,
    });
  });

  it("flush 已提交 + 空助手 LLM_RATE_LIMIT → 回滚 idle（不换 id 也能滚）", async () => {
    sidecarStreamMock.mockImplementation(async (opts) => {
      reportCommitted(opts);
      persistEmptyAssistantFailure({ reconcile: false });
      return undefined as never;
    });

    const result = await sendTurn(spec());

    expect(streamMock).not.toHaveBeenCalled();
    expect(result.unstartedRefusal).toBe(true);
    expect(result.supportPack?.conversationId).toBe(CID);
    expect(result.supportPack?.userMessageId).toBe("opt-u2");
    expect(result.supportPack?.errorCode).toBe("LLM_RATE_LIMIT");
    const rt = getRuntime(CID);
    expect(rt.messages).toHaveLength(0);
    expect(rt.isGenerating).toBe(false);
    expect(rt.turnPhase).toBe("idle");
    expect(rt.error).toBeTruthy();
  });

  it("flush 已提交 + 空助手 LLM_KEY_INVALID → 同样回滚", async () => {
    sidecarStreamMock.mockImplementation(async (opts) => {
      reportCommitted(opts);
      persistEmptyAssistantFailure({
        reconcile: false,
        code: "LLM_KEY_INVALID",
      });
      return undefined as never;
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(true);
    expect(result.supportPack?.errorCode).toBe("LLM_KEY_INVALID");
    expect(getRuntime(CID).messages).toHaveLength(0);
    expect(getRuntime(CID).turnPhase).toBe("idle");
  });

  it("flush 未成功：空助手限流也不滚（乐观 id 仍在列表）", async () => {
    sidecarStreamMock.mockImplementation(async () => {
      persistEmptyAssistantFailure({ reconcile: false });
      return undefined as never;
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(false);
    expect(getRuntime(CID).messages.some((m) => m.id === "opt-u2")).toBe(true);
    expect(getRuntime(CID).turnPhase).toBe("failed");
  });

  it("有正文不滚", async () => {
    sidecarStreamMock.mockImplementation(async (opts) => {
      reportCommitted(opts);
      persistEmptyAssistantFailure({ reconcile: false, content: "半句" });
      return undefined as never;
    });

    const result = await sendTurn(spec());

    expect(result.unstartedRefusal).toBe(false);
    expect(getRuntime(CID).messages.some((m) => m.id === "opt-u2")).toBe(true);
    expect(getRuntime(CID).turnPhase).toBe("failed");
  });
});
