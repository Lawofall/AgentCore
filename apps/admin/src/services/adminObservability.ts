import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

export type AdminObservabilitySummary =
  components["schemas"]["AdminObservabilitySummary"];
export type TurnHealthWindow = components["schemas"]["TurnHealthWindow"];
export type DailyTurns = components["schemas"]["DailyTurns"];
export type TurnMetricLine = components["schemas"]["TurnMetricLine"];
export type AdminConversationReplay =
  components["schemas"]["AdminConversationReplay"];
export type AdminReplayTurnFinalState =
  components["schemas"]["AdminReplayTurnFinalState"];
export type ReplayMessage = components["schemas"]["ReplayMessage"];
export type ReplayConversation = components["schemas"]["ReplayConversation"];
export type ReplaySpan = components["schemas"]["ReplaySpan"];
export type ReplayRun = components["schemas"]["ReplayRun"];

/**
 * 运营观测看板: platform-wide turn health (today + trailing 7 days), the 7-day
 * trend, and the most recent errored turns — sourced from `turn_metrics`, not the
 * dev log file (so it works under prod's stdout-only logging posture).
 */
export async function fetchObservabilitySummary(): Promise<AdminObservabilitySummary> {
  return api.get<AdminObservabilitySummary>("/v1/admin/observability/summary");
}

/**
 * 会话复盘: one conversation's merged timeline — the message thread overlaid with
 * each turn's outcome/quality (turn_metrics) and spend (cost_events), joined by
 * trace_id / message_id. The drill-down target of the 近期错误 feed.
 */
export async function fetchConversationReplay(
  conversationId: string,
): Promise<AdminConversationReplay> {
  return api.get<AdminConversationReplay>(
    `/v1/admin/observability/conversations/${encodeURIComponent(conversationId)}`,
  );
}

/**
 * One assistant turn's user-end final state (`runs_payload` + `projected`).
 * The conversation list omits this pair; the replay page auto-fetches it for
 * `has_final_state` rows in the loaded window (anchor first, concurrency 2).
 */
export async function fetchReplayTurnFinalState(
  conversationId: string,
  messageId: string,
): Promise<AdminReplayTurnFinalState> {
  return api.get<AdminReplayTurnFinalState>(
    `/v1/admin/observability/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/final-state`,
  );
}
