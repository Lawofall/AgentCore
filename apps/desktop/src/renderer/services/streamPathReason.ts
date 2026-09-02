/**
 * Desktop → cloud turn routing reason.
 *
 * Written to ``turn.stream_path`` (desktop.jsonl) and, on the cloud POST, to
 * ``X-AgentCore-Stream-Path-Reason`` so ``log_timeline`` Head can show why a
 * local-bound conversation ran ``via=cloud``. Server allowlists the same
 * enum (``RequestAttributionMiddleware``); unknown values are dropped.
 */
export const STREAM_PATH_REASON_HEADER = "X-AgentCore-Stream-Path-Reason";

export type CloudStreamPathReason =
  | "switch_off"
  | "no_local_engine"
  | "probe_unhealthy"
  | "probe_cache_bad"
  | "no_local_target"
  | "sidecar_fallback";

export function streamPathReasonHeaders(
  reason: CloudStreamPathReason | undefined,
): Record<string, string> {
  if (!reason) return {};
  return { [STREAM_PATH_REASON_HEADER]: reason };
}
