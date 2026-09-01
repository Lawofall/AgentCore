import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Drive the回填 through the REAL api.post by stubbing global fetch (not by
// mocking the api module). vitest instruments its own mock fns and surfaces
// their rejected-promise results as failures even when the SUT catches them; a
// rejection from the real request (user code) is tracked normally, so the
// stale-404 swallow can be asserted cleanly. Mirrors auth.test.ts.
import { BASE_URL } from "@/services/api";
import {
  WORKSPACE_RECONNECT_DETAIL,
  failInflightClientToolsForReconnect,
  resetClientToolFulfillmentForTests,
} from "@/services/clientToolFulfill";
import { resolveConversationLocalTarget } from "@/services/sidecarRouting";
import {
  performWorkspaceOp,
  resetWorkspaceOpIpcInflightForTests,
} from "@/services/workspaceOps";
import { useWorkspaceChannelStore } from "@/stores/workspaceChannel";
import type { WorkspaceOpRequiredPayload } from "@/types/events";

vi.mock("@/services/sidecarRouting", () => ({
  resolveConversationLocalTarget: vi.fn(() => Promise.resolve(null)),
  // interaction.ts（回填入口）也 import 本模块：null = 云路由，测试断言 HTTP 回填。
  getActiveSidecarTarget: vi.fn(() => null),
}));

const resolveTarget = vi.mocked(resolveConversationLocalTarget);

const payload = (
  over: Partial<WorkspaceOpRequiredPayload> = {},
): WorkspaceOpRequiredPayload => ({
  request_id: "r1",
  conversation_id: "c1",
  root_id: "root-1",
  op: "read",
  args: { path: "a.txt" },
  ...over,
});

const stubFsApi = (workspaceOp: unknown) =>
  vi.stubGlobal("window", { fsApi: { workspaceOp } });

const OPS_URL = `${BASE_URL}/v1/conversations/c1/interactions/r1`;

// `headers.get` is read by api.request's captureCsrf before the status check.
const noHeaders = { get: () => null };

// Minimal Response stand-ins for the two outcomes request() cares about.
const okResponse = () => ({
  ok: true,
  status: 200,
  headers: noHeaders,
  // api.request reads body via text() then JSON.parse (not response.json()).
  text: async () => "{}",
});
const errResponse = (status: number, body: string) => ({
  ok: false,
  status,
  headers: noHeaders,
  text: async () => body,
});

// The body request() POSTs, parsed back from the fetch call (init.body is JSON).
const postedBody = (fetchMock: ReturnType<typeof vi.fn>, call = 0) =>
  JSON.parse((fetchMock.mock.calls[call][1] as RequestInit).body as string);

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  resetClientToolFulfillmentForTests();
  resetWorkspaceOpIpcInflightForTests();
  useWorkspaceChannelStore.setState({ notReady: false });
  fetchMock = vi.fn().mockResolvedValue(okResponse());
  vi.stubGlobal("fetch", fetchMock);
  resolveTarget.mockReset();
  resolveTarget.mockResolvedValue(null);
});
afterEach(() => {
  resetClientToolFulfillmentForTests();
  resetWorkspaceOpIpcInflightForTests();
  useWorkspaceChannelStore.setState({ notReady: false });
  vi.unstubAllGlobals();
});

describe("performWorkspaceOp (本地工作区 op 回填)", () => {
  it("runs the op on the bound root and posts the ok result (client_tool kind)", async () => {
    const workspaceOp = vi.fn().mockResolvedValue({ ok: true, value: "hello" });
    stubFsApi(workspaceOp);

    await performWorkspaceOp(payload(), "c1", "cloud");

    expect(workspaceOp).toHaveBeenCalledWith(
      "root-1",
      "read",
      { path: "a.txt" },
      undefined,
      { conversationId: "c1", requestId: "r1" },
    );
    expect(fetchMock.mock.calls[0][0]).toBe(OPS_URL);
    expect(postedBody(fetchMock)).toEqual({
      kind: "client_tool",
      ok: true,
      value: "hello",
    });
  });

  it("injects conversation_id into process_* op args (channel context)", async () => {
    const workspaceOp = vi.fn().mockResolvedValue({
      ok: true,
      value: { processes: [] },
    });
    stubFsApi(workspaceOp);

    await performWorkspaceOp(
      payload({ op: "process_list", args: {} }),
      "c1",
      "cloud",
    );

    expect(workspaceOp).toHaveBeenCalledWith(
      "root-1",
      "process_list",
      { conversation_id: "c1" },
      undefined,
      { conversationId: "c1", requestId: "r1" },
    );
  });

  it("resolves the bound root for a sidecar process op (empty root_id) and prefixes the scratch subpath into start cwd", async () => {
    const workspaceOp = vi.fn().mockResolvedValue({
      ok: true,
      value: { process_id: "p1", status: "running", output: "" },
    });
    stubFsApi(workspaceOp);
    resolveTarget.mockResolvedValue({
      rootId: "container-1",
      subpath: "conv-c1",
    });

    await performWorkspaceOp(
      payload({
        op: "process_start",
        root_id: "",
        args: { command: "pnpm dev", cwd: "web" },
      }),
      "c1",
      "cloud",
    );

    expect(workspaceOp).toHaveBeenCalledWith(
      "container-1",
      "process_start",
      {
        command: "pnpm dev",
        cwd: "conv-c1/web",
        conversation_id: "c1",
      },
      undefined,
      { conversationId: "c1", requestId: "r1" },
    );
  });

  it("keeps worker 异根 process_start on the target root and does not hijack cwd with session scratch subpath", async () => {
    const workspaceOp = vi.fn().mockResolvedValue({
      ok: true,
      value: { process_id: "p1", status: "running", output: "" },
    });
    stubFsApi(workspaceOp);
    resolveTarget.mockResolvedValue({
      rootId: "session-root",
      subpath: "conversations/c1",
    });

    await performWorkspaceOp(
      payload({
        op: "process_start",
        root_id: "worker-other-root",
        args: { command: "pnpm test", cwd: "apps/web" },
      }),
      "c1",
      "cloud",
    );

    expect(workspaceOp).toHaveBeenCalledWith(
      "worker-other-root",
      "process_start",
      {
        command: "pnpm test",
        cwd: "apps/web",
        conversation_id: "c1",
      },
      undefined,
      { conversationId: "c1", requestId: "r1" },
    );
  });

  it("still prefixes session scratch when process_start root_id matches the bound root", async () => {
    const workspaceOp = vi.fn().mockResolvedValue({
      ok: true,
      value: { process_id: "p1", status: "running", output: "" },
    });
    stubFsApi(workspaceOp);
    resolveTarget.mockResolvedValue({
      rootId: "session-root",
      subpath: "conversations/c1",
    });

    await performWorkspaceOp(
      payload({
        op: "process_start",
        root_id: "session-root",
        args: { command: "pnpm dev", cwd: "web" },
      }),
      "c1",
      "cloud",
    );

    expect(workspaceOp).toHaveBeenCalledWith(
      "session-root",
      "process_start",
      {
        command: "pnpm dev",
        cwd: "conversations/c1/web",
        conversation_id: "c1",
      },
      undefined,
      { conversationId: "c1", requestId: "r1" },
    );
  });

  it("resolves the bound root for a sidecar diagnostics op (empty root_id) and prefixes scratch onto paths", async () => {
    const workspaceOp = vi.fn().mockResolvedValue({
      ok: true,
      value: { status: "ok", diagnostics: [] },
    });
    stubFsApi(workspaceOp);
    resolveTarget.mockResolvedValue({
      rootId: "container-1",
      subpath: "conv-c1",
    });

    await performWorkspaceOp(
      payload({
        op: "diagnostics",
        root_id: "",
        args: { paths: ["src/a.ts"] },
      }),
      "c1",
      "cloud",
    );

    expect(workspaceOp).toHaveBeenCalledWith(
      "container-1",
      "diagnostics",
      { paths: ["conv-c1/src/a.ts"] },
      undefined,
      { conversationId: "c1", requestId: "r1" },
    );
  });

  it("does not re-prefix diagnostics paths when payload already has a root_id (过桥)", async () => {
    const workspaceOp = vi.fn().mockResolvedValue({
      ok: true,
      value: { status: "ok", diagnostics: [] },
    });
    stubFsApi(workspaceOp);
    resolveTarget.mockResolvedValue({
      rootId: "session-root",
      subpath: "conversations/c1",
    });

    await performWorkspaceOp(
      payload({
        op: "diagnostics",
        root_id: "session-root",
        args: { paths: ["conversations/c1/src/a.ts"] },
      }),
      "c1",
      "cloud",
    );

    expect(workspaceOp).toHaveBeenCalledWith(
      "session-root",
      "diagnostics",
      { paths: ["conversations/c1/src/a.ts"] },
      undefined,
      { conversationId: "c1", requestId: "r1" },
    );
  });

  it("answers with an IO error when a sidecar diagnostics op has no local binding", async () => {
    const workspaceOp = vi.fn();
    stubFsApi(workspaceOp);

    await performWorkspaceOp(
      payload({ op: "diagnostics", root_id: "", args: { paths: ["a.ts"] } }),
      "c1",
      "cloud",
    );

    expect(workspaceOp).not.toHaveBeenCalled();
    const body = postedBody(fetchMock) as {
      ok: boolean;
      error: { kind: string };
    };
    expect(body.ok).toBe(false);
    expect(body.error.kind).toBe("WorkspaceIOError");
  });

  it("answers with an IO error when a sidecar process op has no local binding", async () => {
    const workspaceOp = vi.fn();
    stubFsApi(workspaceOp);

    await performWorkspaceOp(
      payload({ op: "process_list", root_id: "", args: {} }),
      "c1",
      "cloud",
    );

    expect(workspaceOp).not.toHaveBeenCalled();
    const body = postedBody(fetchMock) as {
      ok: boolean;
      error: { kind: string };
    };
    expect(body.ok).toBe(false);
    expect(body.error.kind).toBe("WorkspaceIOError");
  });

  it("posts a typed error envelope (kind survives for the tool layer)", async () => {
    stubFsApi(
      vi.fn().mockResolvedValue({
        ok: false,
        error: { kind: "PathNotFound", detail: "x" },
      }),
    );

    await performWorkspaceOp(payload(), "c1", "cloud");

    expect(postedBody(fetchMock)).toEqual({
      kind: "client_tool",
      ok: false,
      error: { kind: "PathNotFound", detail: "x" },
    });
  });

  it("answers with an IO error when there is no desktop fsApi (web runtime)", async () => {
    vi.stubGlobal("window", {}); // no fsApi

    await performWorkspaceOp(payload(), "c1", "cloud");

    const body = postedBody(fetchMock) as {
      ok: boolean;
      error: { kind: string };
    };
    expect(body.ok).toBe(false);
    expect(body.error.kind).toBe("WorkspaceIOError");
  });

  it("turns a thrown IPC error into an IO error envelope (never leaves the op unanswered)", async () => {
    stubFsApi(vi.fn().mockRejectedValue(new Error("ipc boom")));

    await performWorkspaceOp(payload(), "c1", "cloud");

    const body = postedBody(fetchMock) as {
      ok: boolean;
      error: { kind: string; detail: string };
    };
    expect(body.ok).toBe(false);
    expect(body.error.detail).toContain("ipc boom");
  });

  it("swallows a stale 404 from the resolve endpoint", async () => {
    stubFsApi(vi.fn().mockResolvedValue({ ok: true, value: "x" }));
    fetchMock.mockResolvedValue(errResponse(404, "gone"));

    await expect(
      performWorkspaceOp(payload(), "c1", "cloud"),
    ).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not re-run the workspace op on a second perform with the same request_id", async () => {
    const workspaceOp = vi.fn().mockResolvedValue({ ok: true, value: "hello" });
    stubFsApi(workspaceOp);

    await performWorkspaceOp(payload(), "c1", "cloud");
    await performWorkspaceOp(payload(), "c1", "cloud");

    expect(workspaceOp).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("aborts on timeout_ms and posts liveness IO error", async () => {
    const workspaceOp = vi.fn(
      () =>
        new Promise<{ ok: true; value: string }>((resolve) => {
          setTimeout(() => resolve({ ok: true, value: "late" }), 500);
        }),
    );
    stubFsApi(workspaceOp);

    await performWorkspaceOp(
      payload({ request_id: "r-abort", timeout_ms: 20 }),
      "c1",
      "cloud",
    );

    expect(workspaceOp).toHaveBeenCalledWith(
      "root-1",
      "read",
      { path: "a.txt" },
      20,
      { conversationId: "c1", requestId: "r-abort" },
    );
    const body = postedBody(fetchMock) as {
      ok: boolean;
      error: { kind: string; detail: string };
    };
    expect(body.ok).toBe(false);
    expect(body.error.kind).toBe("WorkspaceIOError");
    expect(body.error.detail).toContain("活性挂起");
    expect(useWorkspaceChannelStore.getState().notReady).toBe(true);
  });

  it("reconnect abort posts retryable IO error and does not raise the file-channel banner", async () => {
    let started!: () => void;
    const ready = new Promise<void>((resolve) => {
      started = resolve;
    });
    const workspaceOp = vi.fn(
      () =>
        new Promise<{ ok: true; value: string }>(() => {
          started();
        }),
    );
    stubFsApi(workspaceOp);

    const done = performWorkspaceOp(
      payload({ request_id: "r-re" }),
      "c1",
      "cloud",
    );
    await ready;
    failInflightClientToolsForReconnect("cloud");
    await done;

    const body = postedBody(fetchMock) as {
      ok: boolean;
      error: { kind: string; detail: string };
    };
    expect(body.ok).toBe(false);
    expect(body.error.kind).toBe("WorkspaceIOError");
    expect(body.error.detail).toBe(WORKSPACE_RECONNECT_DETAIL);
    expect(useWorkspaceChannelStore.getState().notReady).toBe(false);
  });

  it("regression: cloud origin settles via HTTP while a sidecar turn is active", async () => {
    const { getActiveSidecarTarget } = await import(
      "@/services/sidecarRouting"
    );
    vi.mocked(getActiveSidecarTarget).mockReturnValue({
      rootId: "root-sidecar",
      subpath: "scratch/c1",
      turnId: "turn-local",
    });
    const respond = vi.fn().mockResolvedValue({ resolved: true });
    const workspaceOp = vi.fn().mockResolvedValue({ ok: true, value: "x" });
    vi.stubGlobal("window", {
      fsApi: { workspaceOp },
      sidecarApi: { respond },
    });

    await performWorkspaceOp(payload({ request_id: "r-cloud" }), "c1", "cloud");

    expect(respond).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE_URL}/v1/conversations/c1/interactions/r-cloud`,
    );
    expect(postedBody(fetchMock)).toEqual({
      kind: "client_tool",
      ok: true,
      value: "x",
    });
  });
});
