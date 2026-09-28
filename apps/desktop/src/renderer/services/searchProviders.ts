import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

export type SearchProvidersResponse = Schemas["SearchProvidersResponse"];
export type SearchProvider = Schemas["SearchProviderResponse"];
export type SearchProviderProtocol = SearchProvider["protocol"];
export type CreateSearchProviderRequest =
  Schemas["CreateSearchProviderRequest"];

export function getSearchProviders(): Promise<SearchProvidersResponse> {
  return api.get<SearchProvidersResponse>("/v1/users/me/search-providers");
}

export function createSearchProvider(
  body: CreateSearchProviderRequest,
): Promise<SearchProvider> {
  return api.post<SearchProvider>("/v1/users/me/search-providers", body);
}

export function selectSearchProvider(
  providerId: string | null,
): Promise<SearchProvidersResponse> {
  return api.put<SearchProvidersResponse>(
    "/v1/users/me/search-providers/selection",
    { provider_id: providerId },
  );
}

export function deleteSearchProvider(providerId: string): Promise<void> {
  return api.delete(`/v1/users/me/search-providers/${providerId}`);
}

export function testSearchProvider(
  providerId: string,
): Promise<SearchProvider> {
  return api.post<SearchProvider>(
    `/v1/users/me/search-providers/${providerId}/test`,
  );
}
