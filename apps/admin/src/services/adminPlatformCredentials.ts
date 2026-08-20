import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

/** Per-credential declared upstream tool-surface caps. Null / omitted = unlimited. */
export type ToolSurfaceLimits = {
  max_tools?: number | null;
  max_properties_total?: number | null;
  max_properties_per_tool?: number | null;
};

type GeneratedView = components["schemas"]["PlatformCredentialView"];
type GeneratedCreate = components["schemas"]["CreatePlatformCredentialRequest"];
type GeneratedUpdate = components["schemas"]["UpdatePlatformCredentialRequest"];

export type PlatformCredentialView = GeneratedView & {
  tool_surface_limits?: ToolSurfaceLimits;
};
export type PlatformCredentialListResponse = Omit<
  components["schemas"]["PlatformCredentialListResponse"],
  "data"
> & { data: PlatformCredentialView[] };
export type CreatePlatformCredentialRequest = GeneratedCreate & {
  tool_surface_limits?: ToolSurfaceLimits;
};
export type UpdatePlatformCredentialRequest = GeneratedUpdate & {
  tool_surface_limits?: ToolSurfaceLimits | null;
};

export async function listPlatformCredentials(): Promise<PlatformCredentialListResponse> {
  return api.get<PlatformCredentialListResponse>(
    "/v1/admin/platform-credentials",
  );
}

export async function createPlatformCredential(
  body: CreatePlatformCredentialRequest,
): Promise<PlatformCredentialView> {
  return api.post<PlatformCredentialView>(
    "/v1/admin/platform-credentials",
    body,
  );
}

export async function updatePlatformCredential(
  credentialId: string,
  body: UpdatePlatformCredentialRequest,
): Promise<PlatformCredentialView> {
  return api.patch<PlatformCredentialView>(
    `/v1/admin/platform-credentials/${credentialId}`,
    body,
  );
}

export async function deletePlatformCredential(
  credentialId: string,
): Promise<void> {
  await api.delete(`/v1/admin/platform-credentials/${credentialId}`);
}

export async function clearPlatformCredentialRuntime(
  credentialId: string,
): Promise<PlatformCredentialView> {
  return api.post<PlatformCredentialView>(
    `/v1/admin/platform-credentials/${credentialId}/clear-runtime`,
  );
}
