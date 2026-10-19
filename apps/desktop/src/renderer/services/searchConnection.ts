import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

export type SearchConnectionResponse = Schemas["SearchConnectionResponse"];
export type SearchEngine = SearchConnectionResponse["selected"];

export function getSearchConnection(): Promise<SearchConnectionResponse> {
  return api.get<SearchConnectionResponse>("/v1/users/me/search-connection");
}

export function selectSearchEngine(
  engine: SearchEngine,
): Promise<SearchConnectionResponse> {
  return api.put<SearchConnectionResponse>(
    "/v1/users/me/search-connection/selection",
    { engine },
  );
}

export function connectDeepSeekFromProvider(): Promise<SearchConnectionResponse> {
  return api.post<SearchConnectionResponse>(
    "/v1/users/me/search-connection/deepseek",
  );
}

export function disconnectDeepSeek(): Promise<SearchConnectionResponse> {
  return api.delete<SearchConnectionResponse>(
    "/v1/users/me/search-connection/deepseek",
  );
}
