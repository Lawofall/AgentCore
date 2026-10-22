import { queryClient } from "@/lib/queryClient";
import { externalGrantKeys } from "@/lib/queryKeys";
import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

/** One conversation external mount (server registration; no abs path). */
export type ExternalGrantItem = Schemas["ExternalGrantItem"];

export function externalGrantModeLabel(
  mode: ExternalGrantItem["mode"],
): string {
  if (mode === "organize") return "整理";
  if (mode === "attach_rw") return "可改";
  return "只读";
}

export async function listExternalGrants(
  conversationId: string,
): Promise<ExternalGrantItem[]> {
  const res = await api.get<Schemas["ExternalGrantListResponse"]>(
    `/v1/conversations/${conversationId}/workspace/external-grants`,
  );
  return res.data ?? [];
}

export function invalidateExternalGrants(conversationId: string): void {
  void queryClient.invalidateQueries({
    queryKey: externalGrantKeys.list(conversationId),
  });
}
