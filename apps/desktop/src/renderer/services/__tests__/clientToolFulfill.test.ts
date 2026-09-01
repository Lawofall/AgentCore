import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const resolveInteraction = vi.fn().mockResolvedValue(undefined);
const forceSseTransportDrop = vi.fn().mockReturnValue(false);

vi.mock("@/services/interaction", () => ({
  resolveInteraction: (...args: unknown[]) => resolveInteraction(...args),
}));

vi.mock("@/services/streamConversation", () => ({
  forceSseTransportDrop: (...args: unknown[]) => forceSseTransportDrop(...args),
}));

import { ApiError, NetworkError } from "@/services/api";
import {
  WORKSPACE_RECONNECT_DETAIL,
  abortClientToolRequest,
  failInflightClientToolsForReconnect,
  fulfillClientToolOnce,
  resetClientToolFulfillmentForTests,
} from "../clientToolFulfill";

describe("fulfillClientToolOnce (request_id 在飞/成功去重)", () => {
  beforeEach(() => {
    resetClientToolFulfillmentForTests();
    resolveInteraction.mockReset();
    resolveInteraction.mockResolvedValue(undefined);
    forceSseTransportDrop.mockReset();
    forceSseTransportDrop.mockReturnValue(false);
  });
  afterEach(() => {
    resetClientToolFulfillmentForTests();
    vi.useRealTimers();
  });

  it("runs perform once and resolves with the result", async () => {
    const perform = vi.fn().mockResolvedValue({ ok: true, value: { x: 1 } });

    await fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });

    expect(perform).toHaveBeenCalledTimes(1);
    expect(resolveInteraction).toHaveBeenCalledWith(
      "c1",
      "r1",
      {
        kind: "client_tool",
        ok: true,
        value: { x: 1 },
      },
      "cloud",
    );
  });

  it("skips side effect on a second call with the same request_id after success", async () => {
    const perform = vi.fn().mockResolvedValue({ ok: true, value: "done" });

    await fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });
    await fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });

    expect(perform).toHaveBeenCalledTimes(1);
    expect(resolveInteraction).toHaveBeenCalledTimes(1);
  });

  it("joins in-flight perform so a concurrent redelivery does not double-run", async () => {
    let release!: (v: { ok: true; value: string }) => void;
    const perform = vi.fn(
      () =>
        new Promise<{ ok: true; value: string }>((r) => {
          release = r;
        }),
    );

    const a = fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });
    const b = fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });

    expect(perform).toHaveBeenCalledTimes(1);
    release({ ok: true, value: "once" });
    await Promise.all([a, b]);

    expect(perform).toHaveBeenCalledTimes(1);
    expect(resolveInteraction).toHaveBeenCalledTimes(1);
  });

  it("retries resolve (not perform) when first settle failed and side effect succeeded", async () => {
    const perform = vi.fn().mockResolvedValue({ ok: true, value: "x" });
    // Non-transient Error → no in-process retry; redelivery retries resolve.
    resolveInteraction
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(undefined);

    await fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });
    await fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });

    expect(perform).toHaveBeenCalledTimes(1);
    expect(resolveInteraction).toHaveBeenCalledTimes(2);
  });

  it("retries NetworkError in-process before giving up", async () => {
    vi.useFakeTimers();
    const perform = vi.fn().mockResolvedValue({ ok: true, value: "x" });
    resolveInteraction
      .mockRejectedValueOnce(new NetworkError())
      .mockResolvedValueOnce(undefined);

    const done = fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "workspaceOps",
      perform,
    });
    await vi.runAllTimersAsync();
    await done;

    expect(perform).toHaveBeenCalledTimes(1);
    expect(resolveInteraction).toHaveBeenCalledTimes(2);
    expect(forceSseTransportDrop).not.toHaveBeenCalled();
  });

  it("nudges SSE transport drop after workspace settle retries exhaust", async () => {
    vi.useFakeTimers();
    forceSseTransportDrop.mockReturnValue(true);
    const perform = vi.fn().mockResolvedValue({ ok: true, value: "x" });
    resolveInteraction.mockRejectedValue(new NetworkError());

    const done = fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "workspaceOps",
      perform,
    });
    await vi.runAllTimersAsync();
    await done;

    expect(resolveInteraction).toHaveBeenCalledTimes(3);
    expect(forceSseTransportDrop).toHaveBeenCalledWith("c1");
  });

  it("treats resolve 404 as settled (no-op) and does not re-resolve", async () => {
    const perform = vi.fn().mockResolvedValue({ ok: true, value: "x" });
    resolveInteraction.mockRejectedValue(new ApiError(404, "not found"));

    await fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });
    await fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });

    expect(perform).toHaveBeenCalledTimes(1);
    expect(resolveInteraction).toHaveBeenCalledTimes(1);
  });

  it("allows re-perform after a failed side effect", async () => {
    const perform = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        error: { kind: "Boom", detail: "nope" },
      })
      .mockResolvedValueOnce({ ok: true, value: "ok" });

    await fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });
    await fulfillClientToolOnce({
      requestId: "r1",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "test",
      perform,
    });

    expect(perform).toHaveBeenCalledTimes(2);
    expect(resolveInteraction).toHaveBeenCalledTimes(2);
  });

  it("client_tool_cancelled aborts in-flight perform and skips settle", async () => {
    let signal!: AbortSignal;
    const perform = vi.fn(
      (s: AbortSignal) =>
        new Promise<{ ok: true; value: string }>((_resolve, reject) => {
          signal = s;
          s.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        }),
    );

    const done = fulfillClientToolOnce({
      requestId: "r-cancel",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "workspaceOps",
      perform,
    });
    await vi.waitFor(() => expect(perform).toHaveBeenCalled());
    abortClientToolRequest("r-cancel");
    await expect(done).resolves.toBeUndefined();
    expect(signal.aborted).toBe(true);
    expect(resolveInteraction).not.toHaveBeenCalled();
  });

  it("reconnect abort settles workspace ops with retryable IO error", async () => {
    let signal!: AbortSignal;
    const perform = vi.fn(
      (s: AbortSignal) =>
        new Promise<{ ok: true; value: string }>((_resolve, reject) => {
          signal = s;
          s.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        }),
    );

    const done = fulfillClientToolOnce({
      requestId: "r-reconnect",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "workspaceOps",
      perform,
    });
    await vi.waitFor(() => expect(perform).toHaveBeenCalled());
    failInflightClientToolsForReconnect("cloud");
    await expect(done).resolves.toBeUndefined();
    expect(signal.aborted).toBe(true);
    expect(resolveInteraction).toHaveBeenCalledWith(
      "c1",
      "r-reconnect",
      {
        kind: "client_tool",
        ok: false,
        error: { kind: "WorkspaceIOError", detail: WORKSPACE_RECONNECT_DETAIL },
      },
      "cloud",
    );
  });

  it("reconnect abort does not settle a different origin or non-workspace op", async () => {
    const sidecarPerform = vi.fn(
      (s: AbortSignal) =>
        new Promise<{ ok: true; value: string }>((_resolve, reject) => {
          s.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        }),
    );
    const hostPerform = vi.fn(
      (s: AbortSignal) =>
        new Promise<{ ok: true; value: string }>((_resolve, reject) => {
          s.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        }),
    );
    const sidecarDone = fulfillClientToolOnce({
      requestId: "r-sidecar",
      conversationId: "c1",
      origin: "sidecar",
      logLabel: "workspaceOps",
      perform: sidecarPerform,
    });
    const hostDone = fulfillClientToolOnce({
      requestId: "r-host",
      conversationId: "c1",
      origin: "cloud",
      logLabel: "hostOps",
      perform: hostPerform,
    });
    await vi.waitFor(() => {
      expect(sidecarPerform).toHaveBeenCalled();
      expect(hostPerform).toHaveBeenCalled();
    });
    failInflightClientToolsForReconnect("cloud");
    await Promise.resolve();
    expect(resolveInteraction).not.toHaveBeenCalled();
    abortClientToolRequest("r-sidecar");
    abortClientToolRequest("r-host");
    await Promise.all([sidecarDone, hostDone]);
  });
});
