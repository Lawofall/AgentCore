import { logEvent } from "@/lib/log";
import { performBoardOp } from "@/services/boardOps";
import { performBoardRead } from "@/services/boardRead";
import { abortClientToolRequest } from "@/services/clientToolFulfill";
import { performExternalMount } from "@/services/externalMountOps";
import { performHostOp } from "@/services/hostOps";
import type { InteractionSettleOrigin } from "@/services/interaction";
import { performMcpOp } from "@/services/mcpOps";
import { performWorkspaceOp } from "@/services/workspaceOps";
import type {
  BoardOpRequiredPayload,
  BoardReadRequiredPayload,
  ExternalMountRequiredPayload,
  HostOpRequiredPayload,
  McpOpRequiredPayload,
  WorkspaceOpRequiredPayload,
} from "@/types/events";

/** Re-export settle origin for callers that only import this module. */
export type { InteractionSettleOrigin };

/**
 * Six CLIENT_TOOL `*_required` wire types. Both engines deliver them on a
 * fulfill channel — cloud on the device SSE, sidecar on its stdio push — never
 * on the conversation event stream.
 */
export const CLIENT_TOOL_REQUIRED_TYPES = [
  "workspace_op_required",
  "host_op_required",
  "mcp_op_required",
  "board_op_required",
  "board_read_required",
  "external_mount_required",
] as const;

export type ClientToolRequiredType =
  (typeof CLIENT_TOOL_REQUIRED_TYPES)[number];

const CLIENT_TOOL_REQUIRED_SET: ReadonlySet<string> = new Set(
  CLIENT_TOOL_REQUIRED_TYPES,
);

export function isClientToolRequiredType(
  type: string,
): type is ClientToolRequiredType {
  return CLIENT_TOOL_REQUIRED_SET.has(type);
}

function conversationIdFromPayload(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const cid = (payload as { conversation_id?: unknown }).conversation_id;
  return typeof cid === "string" && cid.length > 0 ? cid : null;
}

/**
 * Run the existing desktop fulfill implementation for one CLIENT_TOOL required
 * frame and settle with an explicit origin (no conversation-wide route guess).
 */
export function dispatchClientToolRequired(
  type: ClientToolRequiredType,
  payload: unknown,
  origin: InteractionSettleOrigin,
): void {
  const conversationId = conversationIdFromPayload(payload);
  if (!conversationId) {
    logEvent("warn", "client_tool.missing_conversation_id", {
      event_type: type,
      origin,
    });
    return;
  }

  switch (type) {
    case "workspace_op_required":
      void performWorkspaceOp(
        payload as WorkspaceOpRequiredPayload,
        conversationId,
        origin,
      );
      return;
    case "host_op_required":
      void performHostOp(
        payload as HostOpRequiredPayload,
        conversationId,
        origin,
      );
      return;
    case "mcp_op_required":
      void performMcpOp(
        payload as McpOpRequiredPayload,
        conversationId,
        origin,
      );
      return;
    case "board_op_required":
      void performBoardOp(
        payload as BoardOpRequiredPayload,
        conversationId,
        origin,
      );
      return;
    case "board_read_required":
      void performBoardRead(
        payload as BoardReadRequiredPayload,
        conversationId,
        origin,
      );
      return;
    case "external_mount_required":
      void performExternalMount(
        payload as ExternalMountRequiredPayload,
        conversationId,
        origin,
      );
      return;
    default: {
      const _exhaustive: never = type;
      void _exhaustive;
    }
  }
}

/** Abort an in-flight CLIENT_TOOL op (`client_tool_cancelled` from fulfill stream). */
export function cancelClientToolByRequestId(requestId: string): void {
  abortClientToolRequest(requestId);
}
