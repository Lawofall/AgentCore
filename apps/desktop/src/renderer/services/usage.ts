import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

/**
 * Cost & usage REST surface — the read side of the「团队工资单 / 账户仪表盘」
 * (§三 / §七).
 *
 * The REST types below are GENERATED from the backend OpenAPI spec
 * (`types/api.generated.ts`, via root `pnpm gen:types`), so they track `api/schemas.py`
 * automatically with zero hand-written drift — this file is the reference slice
 * for that codegen migration (API 开发规范). Money is always integer nano-CNY
 * (1 元 = 1e9); ``CostBreakdown.cny_total`` is already yuan (no FX).
 *
 * The ledger (`cost_events`) is the truth source for spend, so these reads replay
 * a past turn's payroll on reload (the streamed `run_completed.cost` /
 * `message_end.cost` light them up live).
 */

type Schemas = components["schemas"];

/** Token counts (cache_hit + cache_miss == input; reasoning ⊆ output). */
export type UsageBreakdown = Schemas["UsageBreakdown"];
/** A cost in integer nano-CNY plus the server-computed yuan display value. */
export type CostBreakdown = Schemas["CostBreakdown"];
/** One participant's row in the team payroll (one Run = one Agent). */
export type AgentCostLine = Schemas["AgentCostLine"];
/** A turn's cost + per-Agent payroll (`GET /v1/messages/{id}/cost`, 工资单). */
export type TurnCost = Schemas["TurnCost"];
/** Aggregated usage over a time window (today / month). */
export type UsageWindow = Schemas["UsageWindow"];
/** Quota limits (决策④); 0 = unlimited. Money is nano-CNY internally. */
export type QuotaStatus = Schemas["QuotaStatus"];
/** One UTC day's total spend — a point in the dashboard 7-day trend sparkline. */
export type DailyCost = Schemas["DailyCost"];
/** Account dashboard payload (`GET /v1/usage/summary`). */
export type UsageSummary = Schemas["UsageSummary"];
/** One model's call-level spend (`UsageSummary.month_by_model`). */
export type ModelCostLine = Schemas["ModelCostLine"];
/** Platform search uses on the account dashboard. */
export type SearchQuotaStatus = Schemas["SearchQuotaStatus"];
/** One conversation (or AI 改写) on the usage activity list. */
export type UsageActivityLine = Schemas["UsageActivityLine"];
/** Paginated conversation spend (`GET /v1/usage/activity`). */
export type UsageActivityPage = Schemas["UsageActivityPage"];
export type UsageActivityWindow = "today" | "7d" | "month";
/** Current published seat (`UsageSummary.seat` / `POST /v1/seats/redeem`). */
export type SeatStatus = Schemas["SeatStatus"];

/** Account dashboard: today's tokens/cost, the month's cost, and quota. */
export function getUsageSummary(): Promise<UsageSummary> {
  return api.get<UsageSummary>("/v1/usage/summary");
}

/** Conversation-level spend in a window (用量活动表). */
export function getUsageActivity(opts: {
  window?: UsageActivityWindow;
  model?: string;
  page?: number;
  pageSize?: number;
}): Promise<UsageActivityPage> {
  const params = new URLSearchParams();
  if (opts.window) params.set("window", opts.window);
  if (opts.model) params.set("model", opts.model);
  if (opts.page) params.set("page", String(opts.page));
  if (opts.pageSize) params.set("page_size", String(opts.pageSize));
  const qs = params.toString();
  return api.get<UsageActivityPage>(`/v1/usage/activity${qs ? `?${qs}` : ""}`);
}

/** The team payroll for one assistant turn (工资单), rebuilt from the ledger. */
export function getMessageCost(messageId: string): Promise<TurnCost> {
  return api.get<TurnCost>(`/v1/messages/${messageId}/cost`);
}

/** Consume a 链动小铺 card secret and open / extend the matching seat. */
export function redeemSeat(code: string): Promise<SeatStatus> {
  return api.post<SeatStatus>("/v1/seats/redeem", { code });
}
