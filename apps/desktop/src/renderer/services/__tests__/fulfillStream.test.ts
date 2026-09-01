import type { AuthRefreshResult } from "@/services/api";
import type { FsRoot } from "@shared/ipc-contract";
// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const {
  getDeviceIdMock,
  tryRefreshMock,
  notifyUnauthorizedMock,
  isWebRuntimeMock,
  isWebPreviewMock,
  failInflightMock,
} = vi.hoisted(() => ({
  getDeviceIdMock: vi.fn(async () => "device-test-1"),
  tryRefreshMock: vi.fn(async (): Promise<AuthRefreshResult> => "renewed"),
  notifyUnauthorizedMock: vi.fn(),
  isWebRuntimeMock: vi.fn(() => false),
  isWebPreviewMock: vi.fn(() => false),
  failInflightMock: vi.fn(),
}));

vi.mock("@/lib/capabilities", () => ({
  isWebRuntime: () => isWebRuntimeMock(),
}));

vi.mock("@/lib/preview", () => ({
  isWebPreview: () => isWebPreviewMock(),
}));

vi.mock("@/lib/clientBuildInfo", () => ({
  clientHeaders: () => ({ "X-Client-Platform": "desktop" }),
}));

vi.mock("@/services/deviceIdentity", () => ({
  getDeviceId: () => getDeviceIdMock(),
  resetDeviceIdentityForTests: () => undefined,
}));

vi.mock("@/services/api", () => ({
  BASE_URL: "http://localhost:8000",
  getCsrfHeaders: () => ({}),
  captureCsrf: () => undefined,
  tryRefresh: () => tryRefreshMock(),
  notifyUnauthorized: () => notifyUnauthorizedMock(),
}));

vi.mock("@/services/clientToolFulfill", () => ({
  failInflightClientToolsForReconnect: (...args: unknown[]) =>
    failInflightMock(...args),
}));

import {
  FULFILL_CAPS,
  onFulfillFrame,
  resetFulfillStreamForTests,
  startFulfillStream,
  stopFulfillStream,
} from "../fulfillStream";

function flushMicrotasks(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/** A fresh SSE response per fetch — a Response body is single-use. */
function sseResponse(opts: { end?: boolean } = {}): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"type":"ready"}\n\n'));
        if (opts.end) controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

/** One declared query param off a `GET /v1/fulfill` call (`""` = declared empty). */
function declared(call: unknown[] | undefined, param: string): string | null {
  return new URL(String(call?.[0])).searchParams.get(param);
}

/** Roots declared on one `GET /v1/fulfill` call (`""` = declared empty). */
function declaredRoots(call: unknown[] | undefined): string | null {
  return declared(call, "roots");
}

describe("fulfillStream", () => {
  const listRoots = vi.fn(
    async (): Promise<FsRoot[]> => [{ id: "root-a", name: "proj" }],
  );

  beforeEach(() => {
    vi.useRealTimers();
    resetFulfillStreamForTests();
    getDeviceIdMock.mockReset().mockResolvedValue("device-test-1");
    tryRefreshMock.mockReset().mockResolvedValue("renewed");
    notifyUnauthorizedMock.mockReset();
    isWebRuntimeMock.mockReset().mockReturnValue(false);
    isWebPreviewMock.mockReset().mockReturnValue(false);
    failInflightMock.mockReset();
    listRoots.mockReset().mockResolvedValue([{ id: "root-a", name: "proj" }]);
    (window as unknown as { fsApi: { listRoots: typeof listRoots } }).fsApi = {
      listRoots,
    };
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    resetFulfillStreamForTests();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("connects GET /v1/fulfill with device_id, caps, roots", async () => {
    const encoder = new TextEncoder();
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              encoder.encode('event: ready\ndata: {"type":"ready"}\n\n'),
            );
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );

    startFulfillStream();
    await flushMicrotasks();
    await flushMicrotasks();

    expect(fetchMock).toHaveBeenCalled();
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("http://localhost:8000/v1/fulfill?");
    expect(url).toContain("device_id=device-test-1");
    expect(url).toContain(`caps=${encodeURIComponent(FULFILL_CAPS.join(","))}`);
    expect(url).toContain("roots=root-a");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.credentials).toBe("include");
    expect(init.headers).toMatchObject({
      Accept: "text/event-stream",
      "X-Client-Platform": "desktop",
    });

    stopFulfillStream();
  });

  it("fans out ready / *_required / client_tool_cancelled frames", async () => {
    const encoder = new TextEncoder();
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              encoder.encode(
                [
                  'data: {"type":"ready"}\n\n',
                  'data: {"type":"workspace_op_required","payload":{"request_id":"r1"}}\n\n',
                  'data: {"type":"client_tool_cancelled","request_id":"r1"}\n\n',
                ].join(""),
              ),
            );
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );

    const frames: unknown[] = [];
    const unsub = onFulfillFrame((f) => frames.push(f));
    startFulfillStream();
    await flushMicrotasks();
    await flushMicrotasks();

    expect(frames).toEqual([
      { type: "ready" },
      { type: "workspace_op_required", payload: { request_id: "r1" } },
      { type: "client_tool_cancelled", request_id: "r1" },
    ]);
    unsub();
    stopFulfillStream();
  });

  it("on 401 refreshes then reconnects; auth_dead stops and notifies", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.mocked(fetch);
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                new TextEncoder().encode('data: {"type":"ready"}\n\n'),
              );
            },
          }),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      );

    startFulfillStream();
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();

    expect(tryRefreshMock).toHaveBeenCalled();
    // reconnect scheduled with base backoff
    await vi.advanceTimersByTimeAsync(1500);
    await Promise.resolve();
    await Promise.resolve();

    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    stopFulfillStream();

    // auth_dead path
    resetFulfillStreamForTests();
    tryRefreshMock.mockResolvedValueOnce("auth_dead");
    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
    startFulfillStream();
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    expect(notifyUnauthorizedMock).toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    stopFulfillStream();
  });

  it("uses exponential backoff after transport failure", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockRejectedValue(new TypeError("offline"));

    startFulfillStream();
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(failInflightMock).toHaveBeenCalledWith("cloud");

    await vi.advanceTimersByTimeAsync(999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // second failure → 2000ms backoff
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(3);

    stopFulfillStream();
    vi.mocked(Math.random).mockRestore();
  });

  it("只声明永久根：会话授权根由服务端按登记绑到本设备", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async () => sseResponse());
    // `listRoots` 是永久根语义（设置页 / 附件源 / sidecar 路由消费的同一份），
    // 会话授权根不在其中——它随 `external-grants` 登记落到服务端那侧的绑定里。
    listRoots.mockResolvedValue([
      { id: "root-a", name: "proj" },
      { id: "root-b", name: "docs" },
    ]);

    startFulfillStream();
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();

    expect(declaredRoots(fetchMock.mock.calls[0])).toBe("root-a,root-b");
    stopFulfillStream();
  });

  it("读取失败时重连仍声明上次已知 roots", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const fetchMock = vi.mocked(fetch);
    // 每次连上即收尾 → 走真实重连路径
    fetchMock.mockImplementation(async () => sseResponse({ end: true }));

    startFulfillStream();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
    expect(declaredRoots(fetchMock.mock.calls[0])).toBe("root-a");

    listRoots.mockRejectedValue(new Error("IPC 不可用"));
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);

    // 重连的 GET 会整体替换 hub 里的 session：读不到就不能把 roots 缩成空集
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(declaredRoots(fetchMock.mock.calls[1])).toBe("root-a");
    expect(warn).toHaveBeenCalled();

    stopFulfillStream();
    warn.mockRestore();
    vi.mocked(Math.random).mockRestore();
  });

  it("用户撤销全部永久根 → 重连如实声明空集", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async () => sseResponse());

    startFulfillStream();
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
    expect(declaredRoots(fetchMock.mock.calls[0])).toBe("root-a");

    // 撤权后再连：上次已知集合不得把真实空集盖回去
    listRoots.mockResolvedValue([]);
    stopFulfillStream();
    startFulfillStream();
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();

    expect(declaredRoots(fetchMock.mock.calls.at(-1))).toBe("");

    stopFulfillStream();
  });

  it("web 客户端连上同一条流，只当账号态观察者：不声明 caps / roots", async () => {
    isWebRuntimeMock.mockReturnValue(true);
    // 浏览器里 fsApi 是无害桩（listRoots 恒空），但观察者根本不该去问它：
    // 「读不到根」与「不承接本地 op」是两件事，只有后者是 web 的事实。
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async () => sseResponse());

    startFulfillStream();
    await flushMicrotasks();
    await flushMicrotasks();

    expect(fetchMock).toHaveBeenCalled();
    const call = fetchMock.mock.calls[0];
    expect(String(call?.[0])).toContain("http://localhost:8000/v1/fulfill?");
    // 空 caps = 服务端选机永不选中本端；空 roots = 不声明任何本地根。
    expect(declared(call, "caps")).toBe("");
    expect(declaredRoots(call)).toBe("");
    expect(declared(call, "device_id")).toMatch(/^web-/);
    expect(listRoots).not.toHaveBeenCalled();
    // 设备身份是 Electron 专属；web 连接 id 不得走那条路（否则会漏进 X-Client-Device）。
    expect(getDeviceIdMock).not.toHaveBeenCalled();

    stopFulfillStream();
  });

  it("web 观察者收账号态帧（队列快照 / 挂起卡结算）", async () => {
    isWebRuntimeMock.mockReturnValue(true);
    const encoder = new TextEncoder();
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              encoder.encode(
                [
                  'data: {"type":"ready"}\n\n',
                  'data: {"type":"turn_queue_snapshot","payload":{"conversation_id":"c1","items":[]}}\n\n',
                  'data: {"type":"paused_card_settled","payload":{"checkpoint_id":"cp1"}}\n\n',
                ].join(""),
              ),
            );
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );

    const frames: unknown[] = [];
    const unsub = onFulfillFrame((f) => frames.push(f));
    startFulfillStream();
    await flushMicrotasks();
    await flushMicrotasks();

    expect(frames).toEqual([
      { type: "ready" },
      {
        type: "turn_queue_snapshot",
        payload: { conversation_id: "c1", items: [] },
      },
      { type: "paused_card_settled", payload: { checkpoint_id: "cp1" } },
    ]);
    unsub();
    stopFulfillStream();
  });

  it("web 观察者重连沿用同一个连接 id（服务端一台一条，不叠会话）", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    isWebRuntimeMock.mockReturnValue(true);
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async () => sseResponse({ end: true }));

    startFulfillStream();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(declared(fetchMock.mock.calls[1], "device_id")).toBe(
      declared(fetchMock.mock.calls[0], "device_id"),
    );

    stopFulfillStream();
    vi.mocked(Math.random).mockRestore();
  });

  it("no-ops under the offline preview (no backend behind #/preview)", async () => {
    isWebRuntimeMock.mockReturnValue(true);
    isWebPreviewMock.mockReturnValue(true);
    const fetchMock = vi.mocked(fetch);
    startFulfillStream();
    await flushMicrotasks();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
