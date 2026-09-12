import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

export type AdminOverview = components["schemas"]["AdminOverview"];

/**
 * 控制台总览 (landing dashboard): today's pulse (active users / turn health / cost)
 * + account tallies + 7-day cost & turn trends + deployment health + recent errors.
 * A curated one-call view reusing the same aggregates as 用量 / 观测 / 系统 APIs.
 */
export async function fetchOverview(): Promise<AdminOverview> {
  return api.get<AdminOverview>("/v1/admin/overview");
}
