import { logEvent } from "@/lib/log";
import {
  BG_REFRESH_DELAYS_MS,
  refreshAfterBackgroundExecution,
} from "@/services/sse/refreshAfterBackgroundExecution";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { loadLatestWindow } = vi.hoisted(() => ({
  loadLatestWindow: vi.fn(),
}));

vi.mock("@/services/messages", () => ({
  loadLatestWindow,
}));

vi.mock("@/lib/log", () => ({
  logEvent: vi.fn(),
}));

const CID = "conv-bg-refresh";
const logEventMock = vi.mocked(logEvent);

function callsNamed(event: string): unknown[][] {
  return logEventMock.mock.calls.filter((call) => call[1] === event);
}

describe("refreshAfterBackgroundExecution", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    loadLatestWindow.mockReset();
    logEventMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("logs each thrown softRefresh and exhausts after all retries fail", async () => {
    loadLatestWindow.mockImplementation(() =>
      Promise.reject(
        Object.assign(new Error("API 503: unavailable"), {
          name: "ApiError",
          status: 503,
          code: "UNAVAILABLE",
        }),
      ),
    );

    refreshAfterBackgroundExecution(CID);
    await vi.advanceTimersByTimeAsync(0);

    expect(loadLatestWindow).toHaveBeenCalledTimes(1);
    expect(loadLatestWindow).toHaveBeenCalledWith(CID, { softRefresh: true });
    expect(callsNamed("conversation.bg_refresh_failed")).toHaveLength(1);
    expect(callsNamed("conversation.bg_refresh_failed")[0]).toEqual([
      "warn",
      "conversation.bg_refresh_failed",
      expect.objectContaining({
        conversation_id: CID,
        attempt: 1,
        max_attempts: BG_REFRESH_DELAYS_MS.length,
        delay_ms: 0,
        error_name: "ApiError",
        http_status: 503,
        error_code: "UNAVAILABLE",
      }),
    ]);
    expect(callsNamed("conversation.bg_refresh_exhausted")).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(1500);
    expect(loadLatestWindow).toHaveBeenCalledTimes(2);
    expect(callsNamed("conversation.bg_refresh_failed")).toHaveLength(2);
    expect(callsNamed("conversation.bg_refresh_failed")[1]?.[2]).toEqual(
      expect.objectContaining({ attempt: 2, delay_ms: 1500 }),
    );
    expect(callsNamed("conversation.bg_refresh_exhausted")).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(4500);
    expect(loadLatestWindow).toHaveBeenCalledTimes(3);
    expect(callsNamed("conversation.bg_refresh_failed")).toHaveLength(3);
    expect(callsNamed("conversation.bg_refresh_failed")[2]?.[2]).toEqual(
      expect.objectContaining({ attempt: 3, delay_ms: 6000 }),
    );
    expect(callsNamed("conversation.bg_refresh_exhausted")).toEqual([
      [
        "warn",
        "conversation.bg_refresh_exhausted",
        {
          conversation_id: CID,
          attempts: 3,
          failed: 3,
        },
      ],
    ]);
  });

  it("does not log exhaustion when a later retry applies", async () => {
    loadLatestWindow
      .mockRejectedValueOnce(new Error("transient"))
      .mockResolvedValue(true);

    refreshAfterBackgroundExecution(CID);
    await vi.advanceTimersByTimeAsync(0);
    expect(callsNamed("conversation.bg_refresh_failed")).toHaveLength(1);
    expect(callsNamed("conversation.bg_refresh_exhausted")).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(1500);
    await vi.advanceTimersByTimeAsync(4500);
    expect(loadLatestWindow).toHaveBeenCalledTimes(3);
    expect(callsNamed("conversation.bg_refresh_failed")).toHaveLength(1);
    expect(callsNamed("conversation.bg_refresh_exhausted")).toHaveLength(0);
  });

  it("stays quiet when every softRefresh applies", async () => {
    loadLatestWindow.mockResolvedValue(true);

    refreshAfterBackgroundExecution(CID);
    await vi.advanceTimersByTimeAsync(6000);

    expect(loadLatestWindow).toHaveBeenCalledTimes(3);
    expect(logEventMock).not.toHaveBeenCalled();
  });

  it("does not treat gated false returns as thrown failures", async () => {
    loadLatestWindow.mockResolvedValue(false);

    refreshAfterBackgroundExecution(CID);
    await vi.advanceTimersByTimeAsync(6000);

    expect(loadLatestWindow).toHaveBeenCalledTimes(3);
    expect(callsNamed("conversation.bg_refresh_failed")).toHaveLength(0);
    expect(callsNamed("conversation.bg_refresh_exhausted")).toHaveLength(0);
  });
});
