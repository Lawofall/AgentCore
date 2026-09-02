import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/log", () => ({
  logEvent: vi.fn(),
}));

vi.mock("@/services/auth", () => ({
  diagnoseOutage: vi.fn(),
  probeReadyz: vi.fn(),
}));

import { logEvent } from "@/lib/log";
import { diagnoseOutage, probeReadyz } from "@/services/auth";
import type { ReadyzDiagnosis } from "@/services/auth";
import {
  SERVER_HEALTH_FAILURE_THRESHOLD,
  confirmMidSessionOutage,
  probeServerHealth,
  resetServerHealthProbeStateForTests,
} from "@/services/serverHealth";
import { useServerHealthStore } from "@/stores/serverHealth";

const logEventMock = vi.mocked(logEvent);
const diagnoseOutageMock = vi.mocked(diagnoseOutage);
const probeReadyzMock = vi.mocked(probeReadyz);

const OUTAGE_REASON = "连不上 AgentCore 服务，请稍后重试。";

function unreachableDiagnosis(
  extras: Partial<Extract<ReadyzDiagnosis, { ok: false }>> = {},
): ReadyzDiagnosis {
  return {
    ok: false,
    reason: OUTAGE_REASON,
    kind: "network",
    duration_ms: 8,
    ...extras,
  };
}

describe("serverHealth probe hysteresis + api outage confirm", () => {
  beforeEach(() => {
    logEventMock.mockReset();
    diagnoseOutageMock.mockReset();
    probeReadyzMock.mockReset();
    resetServerHealthProbeStateForTests();
    useServerHealthStore.setState({
      status: "checking",
      lastOkAt: null,
      reason: null,
      justRecovered: false,
      offlineSince: null,
    });
  });

  it("does not mark offline before consecutive failure threshold", async () => {
    probeReadyzMock.mockResolvedValue(unreachableDiagnosis());

    for (let i = 1; i < SERVER_HEALTH_FAILURE_THRESHOLD; i++) {
      await expect(probeServerHealth()).resolves.toBe(false);
      expect(useServerHealthStore.getState().status).toBe("checking");
      expect(logEventMock).toHaveBeenCalledWith(
        i <= 1 ? "debug" : "warn",
        "server_health.probe_failed",
        expect.objectContaining({
          consecutive_failures: i,
          failure_threshold: SERVER_HEALTH_FAILURE_THRESHOLD,
          reason: OUTAGE_REASON,
          kind: "network",
          duration_ms: 8,
        }),
      );
    }
    expect(
      logEventMock.mock.calls.filter((c) => c[1] === "server_health.offline"),
    ).toHaveLength(0);
  });

  it("marks offline on the Nth consecutive heartbeat failure", async () => {
    probeReadyzMock.mockResolvedValue(unreachableDiagnosis());

    for (let i = 1; i < SERVER_HEALTH_FAILURE_THRESHOLD; i++) {
      await probeServerHealth();
    }
    logEventMock.mockClear();

    await expect(probeServerHealth()).resolves.toBe(false);
    expect(useServerHealthStore.getState().status).toBe("offline");
    expect(logEventMock).toHaveBeenCalledWith(
      "warn",
      "server_health.offline",
      expect.objectContaining({
        source: "heartbeat",
        consecutive_failures: SERVER_HEALTH_FAILURE_THRESHOLD,
        reason: OUTAGE_REASON,
      }),
    );
  });

  it("resets the soft-failure streak on a healthy probe without offline edge", async () => {
    probeReadyzMock
      .mockResolvedValueOnce(unreachableDiagnosis())
      .mockResolvedValueOnce({ ok: true, duration_ms: 4 });

    await probeServerHealth();
    expect(useServerHealthStore.getState().status).toBe("checking");
    logEventMock.mockClear();

    await expect(probeServerHealth()).resolves.toBe(true);
    expect(useServerHealthStore.getState().status).toBe("online");
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "server_health.probe_recovered",
      expect.objectContaining({ status: "checking" }),
    );
    // Soft streak never flipped UI offline — no online recovery celebration log.
    expect(
      logEventMock.mock.calls.filter((c) => c[1] === "server_health.online"),
    ).toHaveLength(0);
  });

  it("confirmMidSessionOutage ignores when /readyz is healthy", async () => {
    useServerHealthStore.setState({
      status: "online",
      lastOkAt: Date.now() - 1_000,
      reason: null,
      justRecovered: false,
      offlineSince: null,
    });
    diagnoseOutageMock.mockResolvedValue(null);

    await expect(confirmMidSessionOutage()).resolves.toBe(false);
    expect(useServerHealthStore.getState().status).toBe("online");
    expect(logEventMock).toHaveBeenCalledWith(
      "info",
      "server_health.api_outage_ignored",
      expect.objectContaining({ status: "online" }),
    );
    expect(
      logEventMock.mock.calls.filter((c) => c[1] === "server_health.offline"),
    ).toHaveLength(0);
  });

  it("confirmMidSessionOutage marks api_outage when /readyz confirms", async () => {
    useServerHealthStore.setState({
      status: "online",
      lastOkAt: Date.now() - 1_000,
      reason: null,
      justRecovered: false,
      offlineSince: null,
    });
    diagnoseOutageMock.mockResolvedValue(OUTAGE_REASON);

    await expect(confirmMidSessionOutage()).resolves.toBe(true);
    expect(useServerHealthStore.getState().status).toBe("offline");
    expect(logEventMock).toHaveBeenCalledWith(
      "warn",
      "server_health.offline",
      expect.objectContaining({
        source: "api_outage",
        reason: OUTAGE_REASON,
      }),
    );
  });
});
