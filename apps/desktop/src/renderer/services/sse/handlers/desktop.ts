import type { SSEEvent } from "@/types/events";
import type { DispatchContext } from "../types";

/**
 * Desktop CLIENT_TOOL frames (`host` / `mcp` / external_mount) no longer
 * arrive on the conversation SSE bus for cloud turns — they ride the device
 * fulfill stream. Sidecar delivery is handled in `dispatchSSEEvent` before
 * handlers run. This module is intentionally empty (kept so HANDLERS stays
 * stable / import paths do not thrash).
 */
export function handleDesktopEvent(
  _event: SSEEvent,
  _ctx: DispatchContext,
): boolean {
  return false;
}
