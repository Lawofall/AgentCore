import { type McpCatalogServer, listMcpCatalog } from "@/services/mcpCatalog";
import { useCallback, useEffect, useState } from "react";

export type McpCatalogStatus = "absent" | "loading" | "error" | "ready";

/**
 * Desktop-only catalog of enabled MCP tools (same `list_tools` payload the
 * turn discover path uses). Web / missing `mcpApi` stays `absent` — no fake list.
 */
export function useMcpCatalog() {
  const hasApi =
    typeof window !== "undefined" && typeof window.mcpApi?.runOp === "function";
  const [data, setData] = useState<McpCatalogServer[] | null>(null);
  const [status, setStatus] = useState<McpCatalogStatus>(
    hasApi ? "loading" : "absent",
  );
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!hasApi) {
      setStatus("absent");
      setData(null);
      setError(null);
      return () => {};
    }
    let cancelled = false;
    setStatus("loading");
    setError(null);
    listMcpCatalog()
      .then((servers) => {
        if (cancelled) return;
        setData(servers ?? []);
        setStatus("ready");
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [hasApi]);

  useEffect(() => load(), [load]);

  return { data, status, error, reload: () => load() };
}
