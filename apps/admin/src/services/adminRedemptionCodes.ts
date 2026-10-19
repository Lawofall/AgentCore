import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

export type RedemptionCodeLine = Schemas["RedemptionCodeLine"];
export type RedemptionCodeListResponse = Schemas["RedemptionCodeListResponse"];
export type CreateRedemptionCodesResponse =
  Schemas["CreateRedemptionCodesResponse"];
export type PaidPlan = "starter" | "plus" | "flagship";
export type RedemptionStatus = "unused" | "redeemed" | "void";

export async function listRedemptionCodes(opts: {
  sku?: PaidPlan;
  status?: RedemptionStatus;
  limit?: number;
  offset?: number;
}): Promise<RedemptionCodeListResponse> {
  const search = new URLSearchParams();
  if (opts.sku) search.set("sku", opts.sku);
  if (opts.status) search.set("status", opts.status);
  search.set("limit", String(opts.limit ?? 50));
  search.set("offset", String(opts.offset ?? 0));
  return api.get<RedemptionCodeListResponse>(
    `/v1/admin/redemption-codes?${search.toString()}`,
  );
}

export async function createRedemptionCodes(opts: {
  sku: PaidPlan;
  count: number;
}): Promise<CreateRedemptionCodesResponse> {
  return api.post<CreateRedemptionCodesResponse>(
    "/v1/admin/redemption-codes",
    opts,
  );
}

export async function voidRedemptionCode(id: string): Promise<void> {
  await api.post(`/v1/admin/redemption-codes/${id}/void`);
}
