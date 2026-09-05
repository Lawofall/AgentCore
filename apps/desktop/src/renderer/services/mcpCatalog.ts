import type { CapabilityTool } from "@/services/capabilities";

/** One enabled MCP Server after `list_tools` — ready tools, or a failed handshake. */
export interface McpCatalogServer {
  id: string;
  name: string;
  status: "ready" | "failed";
  error?: string;
  tools: CapabilityTool[];
}

const TOOL_NAME_MAX = 64;
const EMPTY_PARAMS: Record<string, unknown> = {
  type: "object",
  properties: {},
};

/**
 * Mint the FC name the model consults (`mcp_{server}_{tool}`), matching
 * `sanitize_mcp_tool_name` in `tools/mcp/dynamic.py`.
 */
export function sanitizeMcpToolName(
  serverId: string,
  toolName: string,
): string {
  const sid =
    (serverId || "srv")
      .trim()
      .replace(/[^a-zA-Z0-9_-]+/g, "_")
      .slice(0, 16) || "srv";
  const tname =
    (toolName || "tool")
      .trim()
      .replace(/[^a-zA-Z0-9_-]+/g, "_")
      .slice(0, 40) || "tool";
  return `mcp_${sid}_${tname}`.slice(0, TOOL_NAME_MAX);
}

function asObjectSchema(input: unknown): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return EMPTY_PARAMS;
  }
  const schema = input as Record<string, unknown>;
  if (schema.type === "object") return schema;
  if ("properties" in schema) {
    return { ...schema, type: "object" };
  }
  return EMPTY_PARAMS;
}

function toCapabilityTool(
  serverId: string,
  serverName: string,
  tool: Record<string, unknown>,
): CapabilityTool | null {
  const mcpName = String(tool.name || "").trim();
  if (!mcpName) return null;
  const description = String(tool.description || "").trim();
  const prefix = `[MCP · ${serverName}] `;
  return {
    name: sanitizeMcpToolName(serverId, mcpName),
    description: prefix + (description || `MCP 工具 ${mcpName}`),
    category: "search",
    approval: "grantable",
    parameters: asObjectSchema(tool.inputSchema ?? tool.input_schema),
    available_to: ["ceo", "worker"],
  };
}

/**
 * Parse a desktop `list_tools` payload. Same shape as `parse_mcp_list_payload`
 * on the server; invalid payload → empty list (caller treats as error).
 */
export function parseMcpListToolsValue(
  value: unknown,
): McpCatalogServer[] | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const servers = (value as { servers?: unknown }).servers;
  if (!Array.isArray(servers)) return null;

  const out: McpCatalogServer[] = [];
  for (const entry of servers) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) continue;
    const rec = entry as Record<string, unknown>;
    const id = String(rec.id || "").trim();
    const name = String(rec.name || id || "MCP").trim() || "MCP";
    const status = String(rec.status || "")
      .trim()
      .toLowerCase();
    if (status !== "ready") {
      out.push({
        id: id || name,
        name,
        status: "failed",
        error: String(rec.error || "握手失败").trim() || "握手失败",
        tools: [],
      });
      continue;
    }
    const rawTools = Array.isArray(rec.tools) ? rec.tools : [];
    const tools: CapabilityTool[] = [];
    for (const tool of rawTools) {
      if (!tool || typeof tool !== "object" || Array.isArray(tool)) continue;
      const mapped = toCapabilityTool(
        id || name,
        name,
        tool as Record<string, unknown>,
      );
      if (mapped) tools.push(mapped);
    }
    out.push({ id: id || name, name, status: "ready", tools });
  }
  return out;
}

/** Load enabled MCP tools from this machine. Missing `mcpApi` → `null` (Web). */
export async function listMcpCatalog(): Promise<McpCatalogServer[] | null> {
  const api = typeof window !== "undefined" ? window.mcpApi : undefined;
  if (!api?.runOp) return null;
  const res = await api.runOp({ op: "list_tools" });
  if (!res.ok) {
    throw new Error(res.error.detail || "列出本机连接器失败");
  }
  const parsed = parseMcpListToolsValue(res.value);
  if (!parsed) {
    throw new Error("列出本机连接器失败");
  }
  return parsed;
}
