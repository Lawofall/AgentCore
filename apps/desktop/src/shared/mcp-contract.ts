/**
 * 本机 MCP Client IPC 契约 —— 主进程 / preload / renderer 三端共享。
 *
 * 服务端经 `mcp_op_required` ClientTool 回填；桌面主进程拉起 stdio MCP Server，
 * 禁止云 API 进程直连本机。
 */

export const MCP_CHANNELS = {
  runOp: "mcp:runOp",
  listServers: "mcp:listServers",
  upsertServer: "mcp:upsertServer",
  removeServer: "mcp:removeServer",
  setServerEnabled: "mcp:setServerEnabled",
  setRunIds: "mcp:setRunIds",
  testServer: "mcp:testServer",
} as const;

export type McpOpName = "list_tools" | "call_tool";

export interface McpOpInput {
  op: McpOpName | string;
  args?: Record<string, unknown>;
}

export type McpOpResult =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: { kind: string; detail: string } };

export interface McpServerConfig {
  id: string;
  name: string;
  enabled: boolean;
  command: string;
  args: string[];
  env?: Record<string, string>;
}

export interface McpServerListItem extends McpServerConfig {
  /** Last known handshake status (best-effort, not durable). */
  runtimeStatus?: "idle" | "ready" | "failed";
  runtimeError?: string;
}

export type McpConfigResult =
  | { ok: true; servers: McpServerListItem[] }
  | { ok: false; error: { kind: string; detail: string } };

export type McpMutationResult =
  | { ok: true; server: McpServerListItem }
  | { ok: false; error: { kind: string; detail: string } };

export type McpTestResult =
  | {
      ok: true;
      status: "ready" | "failed";
      tools: Array<{ name: string; description?: string }>;
      error?: string;
    }
  | { ok: false; error: { kind: string; detail: string } };

export interface McpApi {
  runOp: (input: McpOpInput) => Promise<McpOpResult>;
  listServers: () => Promise<McpConfigResult>;
  upsertServer: (server: McpServerConfig) => Promise<McpMutationResult>;
  removeServer: (id: string) => Promise<McpConfigResult>;
  setServerEnabled: (
    id: string,
    enabled: boolean,
  ) => Promise<McpMutationResult>;
  /** Server ids any assembly enables. Processes stay up for this union. */
  setRunIds: (ids: string[]) => Promise<McpConfigResult>;
  testServer: (id: string) => Promise<McpTestResult>;
}
