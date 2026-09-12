import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

export type AdminSystemStatus = components["schemas"]["AdminSystemStatus"];

/**
 * Shared read-only snapshot (`GET /v1/admin/system`).
 *
 * 供给·额度 owns billing mode + global quota defaults; 总览's deploy snapshot
 * owns database reachability, build provenance, and account tallies. Nothing
 * here is editable (config is env + restart).
 */
export async function fetchSystemStatus(): Promise<AdminSystemStatus> {
  return api.get<AdminSystemStatus>("/v1/admin/system");
}
