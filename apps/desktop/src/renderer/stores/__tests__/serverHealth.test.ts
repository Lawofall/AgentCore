import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/log", () => ({
  logEvent: vi.fn(),
}));

import { logEvent } from "@/lib/log";
import {
  setServerHealthRecoveredHandler,
  useServerHealthStore,
} from "@/stores/serverHealth";

const logEventMock = vi.mocked(logEvent);

describe("useServerHealthStore edge logs", () => {
  beforeEach(() => {
    logEventMock.mockReset();
    useServerHealthStore.setState({
      status: "checking",
      lastOkAt: null,
      reason: null,
      justRecovered: false,
      offlineSince: null,
    });
  });

  afterEach(() => {
    setServerHealthRecoveredHandler(null);
  });

  it("logs server_health.offline once on the offline edge", () => {
    useServerHealthStore
      .getState()
      .markOffline("连不上 AgentCore 服务，请稍后重试。", "heartbeat");

    expect(logEventMock).toHaveBeenCalledTimes(1);
    expect(logEventMock).toHaveBeenCalledWith("warn", "server_health.offline", {
      source: "heartbeat",
      reason: "连不上 AgentCore 服务，请稍后重试。",
      last_ok_at: null,
      from: "checking",
    });
    expect(useServerHealthStore.getState().status).toBe("offline");
    expect(useServerHealthStore.getState().offlineSince).toEqual(
      expect.any(Number),
    );

    useServerHealthStore
      .getState()
      .markOffline("GAgent 服务暂时不可用，请稍后重试。", "heartbeat");
    expect(logEventMock).toHaveBeenCalledTimes(1);
    expect(useServerHealthStore.getState().reason).toBe(
      "GAgent 服务暂时不可用，请稍后重试。",
    );
  });

  it("skips online log on cold-start checking → online", () => {
    useServerHealthStore.getState().markOnline();
    expect(logEventMock).not.toHaveBeenCalled();
    expect(useServerHealthStore.getState().status).toBe("online");
    expect(useServerHealthStore.getState().justRecovered).toBe(false);
  });

  it("logs server_health.online with since_offline_ms on recovery", () => {
    const offlineSince = Date.now() - 1_500;
    useServerHealthStore.setState({
      status: "offline",
      lastOkAt: offlineSince - 10_000,
      reason: "网络已断开，请检查网络连接",
      justRecovered: false,
      offlineSince,
    });

    useServerHealthStore.getState().markOnline();

    expect(logEventMock).toHaveBeenCalledTimes(1);
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "server_health.online",
      expect.objectContaining({
        last_ok_at: offlineSince - 10_000,
        since_offline_ms: expect.any(Number),
      }),
    );
    const logCall = logEventMock.mock.calls[0];
    expect(logCall).toBeTruthy();
    if (!logCall) return;
    const fields = logCall[2] as {
      since_offline_ms: number;
    };
    expect(fields.since_offline_ms).toBeGreaterThanOrEqual(1_500);
    expect(useServerHealthStore.getState().justRecovered).toBe(true);
    expect(useServerHealthStore.getState().offlineSince).toBeNull();
  });

  it("notifies the recovered handler only on offline → online", () => {
    const fn = vi.fn();
    setServerHealthRecoveredHandler(fn);

    useServerHealthStore.getState().markOnline();
    expect(fn).not.toHaveBeenCalled();

    useServerHealthStore.setState({
      status: "offline",
      lastOkAt: Date.now() - 1_000,
      reason: "网络已断开，请检查网络连接",
      justRecovered: false,
      offlineSince: Date.now() - 1_000,
    });
    useServerHealthStore.getState().markOnline();
    expect(fn).toHaveBeenCalledOnce();
  });

  it("logs consecutive_failures on offline edge when provided", () => {
    useServerHealthStore
      .getState()
      .markOffline("连不上 AgentCore 服务，请稍后重试。", "heartbeat", {
        consecutive_failures: 3,
      });

    expect(logEventMock).toHaveBeenCalledWith("warn", "server_health.offline", {
      source: "heartbeat",
      reason: "连不上 AgentCore 服务，请稍后重试。",
      last_ok_at: null,
      from: "checking",
      consecutive_failures: 3,
    });
  });
});
