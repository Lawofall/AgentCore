import { hasLocalMcp } from "@/lib/capabilities";
import { fulfillClientToolOnce } from "@/services/clientToolFulfill";
import type { InteractionSettleOrigin } from "@/services/interaction";
import type { McpOpRequiredPayload } from "@/types/events";
import type { McpOpResult } from "@shared/mcp-contract";

/**
 * Desktop half of the MCP ClientTool channel.
 *
 * After the server suspends and streams ``mcp_op_required``, we run the op in
 * the main process (stdio MCP) and settle over the unified interaction bridge.
 */
export async function performMcpOp(
  payload: McpOpRequiredPayload,
  conversationId: string,
  origin: InteractionSettleOrigin,
): Promise<void> {
  await fulfillClientToolOnce({
    requestId: payload.request_id,
    conversationId,
    origin,
    logLabel: "mcpOps",
    perform: async () => runMcpOp(payload),
  });
}

async function runMcpOp(payload: McpOpRequiredPayload): Promise<McpOpResult> {
  if (!hasLocalMcp() || !window.mcpApi?.runOp) {
    return {
      ok: false,
      error: {
        kind: "McpOpError",
        detail: "非桌面环境，无法履行本机 MCP 操作",
      },
    };
  }
  try {
    return await window.mcpApi.runOp({
      op: payload.op,
      args: (payload.args ?? {}) as Record<string, unknown>,
    });
  } catch (e) {
    return {
      ok: false,
      error: {
        kind: "McpOpError",
        detail: e instanceof Error ? e.message : String(e),
      },
    };
  }
}
