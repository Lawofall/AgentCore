/**
 * Unit tests for MCP config helpers / SDK wiring (no real child process).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const connect = vi.fn();
const listTools = vi.fn();
const callTool = vi.fn();
const clientClose = vi.fn();
const transportClose = vi.fn();
const getDefaultEnvironment = vi.fn(() => ({
  PATH: "/safe/bin",
  HOME: "/home/test",
}));
const onDataHandlers: Array<(chunk: Buffer | string) => void> = [];
const transportCtorArgs: Array<{
  command?: string;
  env?: Record<string, string>;
}> = [];

vi.mock("electron", () => ({
  app: {
    getPath: () => "/tmp/agentcore-mcp-test",
    getVersion: () => "0.0.0-test",
  },
  ipcMain: { handle: vi.fn() },
}));

vi.mock("@modelcontextprotocol/sdk/client/index.js", () => ({
  Client: class {
    connect = connect;
    listTools = listTools;
    callTool = callTool;
    close = clientClose;
  },
}));

vi.mock("@modelcontextprotocol/sdk/client/stdio.js", () => ({
  getDefaultEnvironment: () => getDefaultEnvironment(),
  StdioClientTransport: class {
    onerror?: (error: Error) => void;
    onclose?: () => void;
    stderr = {
      on: vi.fn((event: string, cb: (chunk: Buffer | string) => void) => {
        if (event === "data") onDataHandlers.push(cb);
      }),
    };
    close = transportClose;
    constructor(params: { command?: string; env?: Record<string, string> }) {
      transportCtorArgs.push(params);
    }
  },
}));

vi.mock("node:fs", async () => {
  const actual = await vi.importActual<typeof import("node:fs")>("node:fs");
  return {
    ...actual,
    existsSync: vi.fn(() => true),
    mkdirSync: vi.fn(),
    readFileSync: vi.fn(() =>
      JSON.stringify({
        servers: [
          {
            id: "srv1",
            name: "Echo",
            enabled: true,
            command: "npx",
            args: ["-y", "fake-mcp"],
            env: { MCP_TOKEN: "from-config" },
          },
        ],
      }),
    ),
    writeFileSync: vi.fn(),
  };
});

type RunOpHandler = (
  e: unknown,
  input: { op: string; args?: Record<string, unknown> },
) => Promise<{
  ok: boolean;
  value?: {
    servers?: Array<{ error?: string; status?: string }>;
    content?: unknown;
  };
}>;

async function registerAndGetRunOp(): Promise<RunOpHandler> {
  const { registerMcpIpc } = await import("../mcp-service");
  const { ipcMain } = await import("electron");
  vi.mocked(ipcMain.handle).mockClear();
  registerMcpIpc();
  const runOpCall = vi
    .mocked(ipcMain.handle)
    .mock.calls.find((c) => c[0] === "mcp:runOp");
  if (!runOpCall) throw new Error("mcp:runOp handler not registered");
  return runOpCall[1] as RunOpHandler;
}

describe("mcp-service SDK client", () => {
  beforeEach(() => {
    connect.mockReset();
    listTools.mockReset();
    callTool.mockReset();
    clientClose.mockReset();
    transportClose.mockReset();
    getDefaultEnvironment.mockClear();
    onDataHandlers.length = 0;
    transportCtorArgs.length = 0;
    connect.mockResolvedValue(undefined);
    listTools.mockResolvedValue({
      tools: [
        {
          name: "ping",
          description: "Ping",
          inputSchema: { type: "object", properties: {} },
        },
      ],
    });
    callTool.mockResolvedValue({
      content: [{ type: "text", text: "pong" }],
    });
    clientClose.mockResolvedValue(undefined);
    transportClose.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.resetModules();
  });

  it("exports registerMcpIpc / shutdownAllMcpSessions", async () => {
    const mod = await import("../mcp-service");
    expect(typeof mod.registerMcpIpc).toBe("function");
    expect(typeof mod.shutdownAllMcpSessions).toBe("function");
  });

  it("list_tools uses Client.connect + listTools", async () => {
    const handler = await registerAndGetRunOp();
    const result = await handler({}, { op: "list_tools" });
    expect(connect).toHaveBeenCalled();
    expect(listTools).toHaveBeenCalled();
    expect(result.ok).toBe(true);
    expect(result.value?.servers).toEqual([
      {
        id: "srv1",
        name: "Echo",
        status: "ready",
        tools: [
          {
            name: "ping",
            description: "Ping",
            inputSchema: { type: "object", properties: {} },
          },
        ],
      },
    ]);
  });

  it("published run ids replace the enabled flag", async () => {
    const handler = await registerAndGetRunOp();
    const { ipcMain } = await import("electron");
    const setRunIds = vi
      .mocked(ipcMain.handle)
      .mock.calls.find((call) => call[0] === "mcp:setRunIds");
    if (!setRunIds) throw new Error("mcp:setRunIds handler not registered");
    const publish = setRunIds[1] as (
      event: unknown,
      ids: string[],
    ) => Promise<unknown>;
    await publish({}, []);
    connect.mockClear();
    const result = await handler({}, { op: "list_tools" });
    expect(connect).not.toHaveBeenCalled();
    expect(result.ok).toBe(true);
    expect(result.value?.servers).toEqual([]);
  });

  it("spawnEnv uses getDefaultEnvironment + config.env (not full process.env)", async () => {
    process.env.SECRET_LEAK_PROBE = "should-not-inherit";
    const handler = await registerAndGetRunOp();
    await handler({}, { op: "list_tools" });
    expect(getDefaultEnvironment).toHaveBeenCalled();
    expect(transportCtorArgs.length).toBeGreaterThan(0);
    const env = transportCtorArgs[0]?.env || {};
    expect(env.PATH).toBe("/safe/bin");
    expect(env.HOME).toBe("/home/test");
    expect(env.MCP_TOKEN).toBe("from-config");
    expect(env.SECRET_LEAK_PROBE).toBeUndefined();
    process.env.SECRET_LEAK_PROBE = undefined;
  });

  it("shutdownAllMcpSessions awaits killSession for live sessions", async () => {
    const handler = await registerAndGetRunOp();
    await handler({}, { op: "list_tools" });
    expect(clientClose).not.toHaveBeenCalled();
    const { shutdownAllMcpSessions } = await import("../mcp-service");
    await shutdownAllMcpSessions();
    expect(clientClose).toHaveBeenCalled();
    expect(transportClose).toHaveBeenCalled();
  });

  it("surfaces stderr tail on handshake failure", async () => {
    connect.mockImplementation(async () => {
      for (const cb of onDataHandlers) cb("spawn failed: missing binary\n");
      throw new Error("MCP 握手失败");
    });
    const handler = await registerAndGetRunOp();
    const result = await handler({}, { op: "list_tools" });
    expect(result.ok).toBe(true);
    const err = result.value?.servers?.[0]?.error || "";
    expect(err).toContain("MCP 握手失败");
    expect(err).toContain("MCP stderr");
    expect(err).toContain("spawn failed: missing binary");
  });

  it("call_tool forwards via client.callTool", async () => {
    const handler = await registerAndGetRunOp();
    const result = await handler(
      {},
      {
        op: "call_tool",
        args: {
          server_id: "srv1",
          tool_name: "ping",
          arguments: { x: 1 },
        },
      },
    );
    expect(callTool).toHaveBeenCalledWith({
      name: "ping",
      arguments: { x: 1 },
    });
    expect(result.ok).toBe(true);
    expect(result.value).toEqual({
      content: [{ type: "text", text: "pong" }],
    });
  });
});
