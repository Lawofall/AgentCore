import { hasHostOps } from "@/lib/capabilities";
import { fulfillClientToolOnce } from "@/services/clientToolFulfill";
import type { InteractionSettleOrigin } from "@/services/interaction";
import type { HostOpRequiredPayload } from "@/types/events";
import type { HostOpResult } from "@shared/host-contract";

/**
 * Desktop half of the Host ClientTool channel.
 *
 * After the server suspends and streams ``host_op_required`` (device fulfill
 * stream for cloud; sidecar pump for local turns), we run the op in the main
 * process and settle over the unified interaction bridge (kind ``client_tool``).
 * Same ``request_id`` is de-duplicated in-process so attach rehang does not
 * re-run host side effects (e.g. shell).
 */
export async function performHostOp(
  payload: HostOpRequiredPayload,
  conversationId: string,
  origin: InteractionSettleOrigin,
): Promise<void> {
  await fulfillClientToolOnce({
    requestId: payload.request_id,
    conversationId,
    origin,
    logLabel: "hostOps",
    perform: async () => runHostOp(payload),
  });
}

async function runHostOp(
  payload: HostOpRequiredPayload,
): Promise<HostOpResult> {
  if (!hasHostOps() || !window.hostApi?.runOp) {
    return {
      ok: false,
      error: {
        kind: "HostOpError",
        detail: "非桌面环境，无法履行本机 Host 操作",
      },
    };
  }
  try {
    return await window.hostApi.runOp({
      op: payload.op,
      args: (payload.args ?? {}) as Record<string, unknown>,
    });
  } catch (e) {
    return {
      ok: false,
      error: {
        kind: "HostOpError",
        detail: e instanceof Error ? e.message : String(e),
      },
    };
  }
}
