import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

export type SearchConnectionResponse = Schemas["SearchConnectionResponse"];
export type SearchEngine = SearchConnectionResponse["selected"];
export type OwnSearchEngine = Exclude<SearchEngine, "platform">;

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

export function connectCleverSee(
  apiKey: string,
): Promise<SearchConnectionResponse> {
  return api.post<SearchConnectionResponse>(
    "/v1/users/me/search-connection/cleversee",
    { api_key: apiKey },
  );
}

export function connectSearxng(
  baseUrl: string,
  apiKey: string,
): Promise<SearchConnectionResponse> {
  return api.post<SearchConnectionResponse>(
    "/v1/users/me/search-connection/searxng",
    { base_url: baseUrl, api_key: apiKey },
  );
}

export function disconnectSearchEngine(
  engine: OwnSearchEngine,
): Promise<SearchConnectionResponse> {
  const path =
    engine === "cleversee"
      ? "/v1/users/me/search-connection/cleversee"
      : "/v1/users/me/search-connection/searxng";
  return api.delete<SearchConnectionResponse>(path);
}
