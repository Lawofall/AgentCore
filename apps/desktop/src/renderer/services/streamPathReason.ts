/**
 * Reason stamped on a pure-cloud POST.
 *
 * Local-folder turns that cannot start stay on desktop ``turn.stream_path``
 * (``via=sidecar``) and do not POST. Unknown header values are dropped.
 */
export const STREAM_PATH_REASON_HEADER = "X-AgentCore-Stream-Path-Reason";

export type CloudStreamPathReason =
  | "switch_off"
  | "no_local_engine"
  | "no_local_target";

/** Renderer code on a recoverable sidecar StreamError: occupy never started the engine. */
export const SIDECAR_OCCUPY_FAILED_CODE = "sidecar_occupy_failed";

export function streamPathReasonHeaders(
  reason: CloudStreamPathReason | undefined,
): Record<string, string> {
  if (!reason) return {};
  return { [STREAM_PATH_REASON_HEADER]: reason };
}
