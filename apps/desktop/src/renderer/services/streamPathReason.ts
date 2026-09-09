/**
 * Desktop stream-path reason.
 *
 * Cloud POST still sends ``switch_off`` / ``no_local_engine`` / ``no_local_target`` /
 * ``occupy_failed`` via ``X-AgentCore-Stream-Path-Reason``. Probe / start failures
 * stay on desktop ``turn.stream_path`` (``via=sidecar``) and do not POST.
 * ``probe_*`` / ``sidecar_fallback`` remain in the type so old clients and the
 * server allowlist still parse. Unknown values are dropped.
 */
export const STREAM_PATH_REASON_HEADER = "X-AgentCore-Stream-Path-Reason";

export type CloudStreamPathReason =
  | "switch_off"
  | "no_local_engine"
  | "probe_unhealthy"
  | "probe_cache_bad"
  | "no_local_target"
  | "sidecar_fallback"
  | "occupy_failed";

/** Renderer code on a recoverable sidecar StreamError: occupy never started the engine. */
export const SIDECAR_OCCUPY_FAILED_CODE = "sidecar_occupy_failed";

export function streamPathReasonHeaders(
  reason: CloudStreamPathReason | undefined,
): Record<string, string> {
  if (!reason) return {};
  return { [STREAM_PATH_REASON_HEADER]: reason };
}
