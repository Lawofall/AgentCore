// @vitest-environment jsdom
/**
 * Turn-stream drop must GET-attach with bounded follow-style backoff — never POST.
 */
import { StreamError } from "@/lib/errors";
import {
  RECONNECTING_BANNER,
  RECONNECT_BANNER,
  RECONNECT_FINISHED_BANNER,
  RECONNECT_INTERRUPTED_BANNER,
  UNKNOWN_CLOUD_BANNER,
} from "@/services/turns/helpers";
import { reconnectBackoffMs } from "@/services/turns/reconnectBackoff";
import {
  cancelRejoinLiveTurn,
  handleServerHealthRecovered,
  rejoinLiveTurn,
  resetRejoinLiveTurnForTests,
} from "@/services/turns/recovery";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { setServerHealthRecoveredHandler } from "@/stores/serverHealth";
import { useServerHealthStore } from "@/stores/serverHealth";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStreamOwnershipForTests } from "../turns/streamOwnership";

const attachConversation = vi.hoisted(() => vi.fn());
const loadLatestWindow = vi.hoisted(() => vi.fn(async () => true));
const loadRecovery = vi.hoisted(() =>
  vi.fn(async () => ({
    sidecarLive: false,
    cloudLive: true,
    cloudKnown: true,
    pausedCount: 0,
    unsynced: [],
  })),
);

vi.mock("@/lib/log", () => ({
  logEvent: vi.fn(),
}));

vi.mock("@/services/streamConversation", () => ({
  attachConversation,
  clearLastEventId: vi.fn(),
}));

vi.mock("@/services/messages", () => ({
  loadLatestWindow,
}));

vi.mock("@/services/resume", () => ({
  loadRecovery,
}));

const CID = "conv-rejoin-retry";

function seedUser(): void {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  const conv = useConversationStore.getState();
  conv.switchConversation(CID);
  conv.addMessage({
    id: "u1",
    role: "user",
    content: "这一轮",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
}

async function flush(): Promise<void> {
  for (let i = 0; i < 8; i++) {
    await Promise.resolve();
  }
}

beforeEach(() => {
  seedUser();
  attachConversation.mockReset();
  loadLatestWindow.mockClear();
  loadRecovery.mockReset();
  loadRecovery.mockResolvedValue({
    sidecarLive: false,
    cloudLive: true,
    cloudKnown: true,
    pausedCount: 0,
    unsynced: [],
  });
  vi.spyOn(Math, "random").mockReturnValue(0);
  setServerHealthRecoveredHandler(handleServerHealthRecovered);
  useServerHealthStore.setState({
    status: "checking",
    lastOkAt: null,
    reason: null,
    justRecovered: false,
    offlineSince: null,
  });
});

afterEach(() => {
  resetRejoinLiveTurnForTests();
  resetStreamOwnershipForTests();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

describe("rejoinLiveTurn · bounded GET attach, never resend", () => {
  it("retries attachConversation only — fetch is never used (no POST resend)", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    attachConversation
      .mockRejectedValueOnce(new StreamError("network"))
      .mockResolvedValueOnce("attached");

    vi.useFakeTimers();
    const done = rejoinLiveTurn(CID);
    await flush();
    await done;
    expect(getRuntime(CID).error).toBe(RECONNECT_BANNER);
    expect(loadRecovery).toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(reconnectBackoffMs(0, 0));
    await flush();

    expect(attachConversation).toHaveBeenCalledTimes(2);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(getRuntime(CID).error).toBeNull();
    for (const call of attachConversation.mock.calls) {
      expect(call[0]).toBe(CID);
      expect(call[1]).toBeInstanceOf(AbortSignal);
    }
  });

  it("does not POST or start a new turn when the first attach drops", async () => {
    attachConversation.mockRejectedValue(new StreamError("network"));
    await expect(rejoinLiveTurn(CID)).resolves.toBe(true);
    expect(attachConversation).toHaveBeenCalledTimes(1);
    expect(getRuntime(CID).error).toBe(RECONNECT_BANNER);
    expect(
      getRuntime(CID).messages.filter((m) => m.role === "user"),
    ).toHaveLength(1);
  });

  it("keeps GET-attaching past the old 8-attempt cap while the chat is open", async () => {
    attachConversation.mockRejectedValue(new StreamError("network"));
    vi.useFakeTimers();
    const done = rejoinLiveTurn(CID);
    await flush();
    await done;

    const pastOldCap = 10;
    for (let attempt = 0; attempt < pastOldCap; attempt++) {
      await vi.advanceTimersByTimeAsync(reconnectBackoffMs(attempt, 0));
      await flush();
    }
    expect(attachConversation).toHaveBeenCalledTimes(1 + pastOldCap);
    expect(getRuntime(CID).error).toBe(RECONNECT_BANNER);

    attachConversation.mockResolvedValueOnce("attached");
    await vi.advanceTimersByTimeAsync(reconnectBackoffMs(pastOldCap, 0));
    await flush();
    expect(attachConversation).toHaveBeenCalledTimes(2 + pastOldCap);
    expect(getRuntime(CID).error).toBeNull();
  });

  it("stops retrying when the user leaves the conversation window", async () => {
    attachConversation.mockRejectedValue(new StreamError("network"));
    vi.useFakeTimers();
    const done = rejoinLiveTurn(CID);
    await flush();
    await done;
    expect(attachConversation).toHaveBeenCalledTimes(1);

    useConversationStore.getState().switchConversation("conv-other");
    await vi.advanceTimersByTimeAsync(60_000);
    await flush();
    expect(attachConversation).toHaveBeenCalledTimes(1);
  });

  it("yields to cancelRejoinLiveTurn — no further attach after the user takes over", async () => {
    attachConversation.mockRejectedValue(new StreamError("network"));
    vi.useFakeTimers();
    const done = rejoinLiveTurn(CID);
    await flush();
    await done;
    expect(attachConversation).toHaveBeenCalledTimes(1);

    cancelRejoinLiveTurn(CID);
    await vi.advanceTimersByTimeAsync(60_000);
    await flush();
    expect(attachConversation).toHaveBeenCalledTimes(1);
  });

  it("204 none with no assistant is interrupted — caller may resend; no retry loop", async () => {
    attachConversation.mockResolvedValue("none");
    await expect(rejoinLiveTurn(CID)).resolves.toBe(false);
    expect(loadLatestWindow).toHaveBeenCalledWith(CID);
    expect(getRuntime(CID).error).toBe(RECONNECT_INTERRUPTED_BANNER);
    await vi.waitFor(() => {
      expect(attachConversation).toHaveBeenCalledTimes(1);
    });
  });

  it("204 none with complete assistant → finished banner", async () => {
    attachConversation.mockResolvedValue("none");
    loadLatestWindow.mockImplementation(async () => {
      useConversationStore.getState().addMessage(
        {
          id: "a1",
          role: "assistant",
          content: "写完了",
          createdAt: "",
          executionId: null,
          isStreaming: false,
          status: "complete",
        },
        CID,
      );
      return true;
    });
    await expect(rejoinLiveTurn(CID)).resolves.toBe(true);
    expect(getRuntime(CID).error).toBe(RECONNECT_FINISHED_BANNER);
    expect(attachConversation).toHaveBeenCalledTimes(1);
  });

  it("recovery idle + complete assistant → finished banner, stop retrying", async () => {
    attachConversation.mockRejectedValue(new StreamError("network"));
    loadRecovery.mockResolvedValue({
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });
    loadLatestWindow.mockImplementation(async () => {
      useConversationStore.getState().addMessage(
        {
          id: "a1",
          role: "assistant",
          content: "写完了",
          createdAt: "",
          executionId: null,
          isStreaming: false,
          status: "complete",
        },
        CID,
      );
      return true;
    });
    vi.useFakeTimers();
    await expect(rejoinLiveTurn(CID)).resolves.toBe(true);
    expect(getRuntime(CID).error).toBe(RECONNECT_FINISHED_BANNER);
    await vi.advanceTimersByTimeAsync(60_000);
    await flush();
    expect(attachConversation).toHaveBeenCalledTimes(1);
  });

  it("recovery idle + no complete reply → interrupted banner, stop retrying", async () => {
    attachConversation.mockRejectedValue(new StreamError("network"));
    loadRecovery.mockResolvedValue({
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });
    vi.useFakeTimers();
    await expect(rejoinLiveTurn(CID)).resolves.toBe(true);
    expect(getRuntime(CID).error).toBe(RECONNECT_INTERRUPTED_BANNER);
    await vi.advanceTimersByTimeAsync(60_000);
    await flush();
    expect(attachConversation).toHaveBeenCalledTimes(1);
  });

  it("recovery unknown → honest fallback, keep retrying (never stuck querying)", async () => {
    attachConversation.mockRejectedValue(new StreamError("network"));
    loadRecovery.mockResolvedValue({
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: false,
      pausedCount: 0,
      unsynced: [],
    });
    vi.useFakeTimers();
    await expect(rejoinLiveTurn(CID)).resolves.toBe(true);
    expect(getRuntime(CID).error).toBe(UNKNOWN_CLOUD_BANNER);
    expect(getRuntime(CID).error).not.toBe(RECONNECTING_BANNER);
    await vi.advanceTimersByTimeAsync(reconnectBackoffMs(0, 0));
    await flush();
    expect(attachConversation).toHaveBeenCalledTimes(2);
  });

  it("markOnline after offline clears the reconnect banner and wakes attach", async () => {
    attachConversation
      .mockRejectedValueOnce(new StreamError("network"))
      .mockResolvedValueOnce("attached");
    vi.useFakeTimers();
    const done = rejoinLiveTurn(CID);
    await flush();
    await done;
    expect(getRuntime(CID).error).toBe(RECONNECT_BANNER);

    useServerHealthStore.setState({
      status: "offline",
      lastOkAt: Date.now() - 5_000,
      reason: "网络已断开，请检查网络连接",
      justRecovered: false,
      offlineSince: Date.now() - 5_000,
    });
    useServerHealthStore.getState().markOnline();
    await flush();

    expect(getRuntime(CID).error).toBeNull();
    expect(attachConversation).toHaveBeenCalledTimes(2);
  });

  it("handleServerHealthRecovered kicks a fresh attach when retries already exhausted", async () => {
    useConversationStore.getState().setError(RECONNECT_BANNER, null, CID, null);
    attachConversation.mockResolvedValue("attached");

    handleServerHealthRecovered();
    await flush();

    expect(getRuntime(CID).error).toBeNull();
    expect(attachConversation).toHaveBeenCalledTimes(1);
  });

  it("does not clear an unrelated banner on recovery", () => {
    const other = "请先接入自己的 API Key，再发起对话。";
    useConversationStore.getState().setError(other, null, CID, null);
    handleServerHealthRecovered();
    expect(getRuntime(CID).error).toBe(other);
    expect(attachConversation).not.toHaveBeenCalled();
  });
});
