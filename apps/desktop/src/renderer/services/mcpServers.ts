import { api } from "@/services/api";
import type { components } from "@/types/api.generated";
import type { McpServerListItem } from "@shared/mcp-contract";

type Schemas = components["schemas"];

export type McpServerView = Schemas["McpServerView"];
export type McpServerListResponse = Schemas["McpServerListResponse"];
export type CreateMcpServerInput = Schemas["CreateMcpServerRequest"];
export type UpdateMcpServerInput = Schemas["UpdateMcpServerRequest"];
export type McpServerTestResponse = Schemas["McpServerTestResponse"];
export type McpOAuthStartResponse = Schemas["McpOAuthStartResponse"];

export function listMcpServers(): Promise<McpServerListResponse> {
  return api.get<McpServerListResponse>("/v1/users/me/mcp-servers");
}

export function createMcpServer(
  input: CreateMcpServerInput,
): Promise<McpServerView> {
  return api.post<McpServerView>("/v1/users/me/mcp-servers", input);
}

export function updateMcpServer(
  serverId: string,
  input: UpdateMcpServerInput,
): Promise<McpServerView> {
  return api.patch<McpServerView>(
    `/v1/users/me/mcp-servers/${serverId}`,
    input,
  );
}

export function deleteMcpServer(serverId: string): Promise<{ status: string }> {
  return api.delete<{ status: string }>(`/v1/users/me/mcp-servers/${serverId}`);
}

export function testMcpServer(
  serverId: string,
): Promise<McpServerTestResponse> {
  return api.post<McpServerTestResponse>(
    `/v1/users/me/mcp-servers/${serverId}/test`,
  );
}

export function startMcpOAuth(
  serverId: string,
  returnOrigin: string,
): Promise<McpOAuthStartResponse> {
  return api.post<McpOAuthStartResponse>(
    `/v1/users/me/mcp-servers/${serverId}/oauth/start`,
    { return_origin: returnOrigin },
  );
}

export function remoteMcpToListItem(view: McpServerView): McpServerListItem {
  const status = view.status;
  return {
    id: view.id,
    name: view.name,
    enabled: true,
    command: "",
    args: [],
    transport: "http",
    url: view.url,
    authKind: view.auth_kind as McpServerListItem["authKind"],
    hasToken: view.has_token,
    runtimeStatus:
      status === "ready"
        ? "ready"
        : status === "failed"
          ? "failed"
          : status === "needs_auth"
            ? "needs_auth"
            : "idle",
    runtimeError: view.last_error || undefined,
  };
}
