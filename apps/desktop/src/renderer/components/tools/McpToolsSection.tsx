import { Button, CatalogIconShell } from "@/components/ui";
import { artifactColorVar } from "@/lib/catalogColors";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import type { McpCatalogServer } from "@/services/mcpCatalog";
import { Loader2, Plug } from "lucide-react";
import { Link } from "react-router-dom";
import { ToolCard } from "./ToolCard";
import { useMcpCatalog } from "./useMcpCatalog";

function SectionHead({
  count,
}: {
  count: number | null;
}) {
  const colorVar = artifactColorVar("connectors");
  return (
    <div className="mb-2 flex items-center justify-between gap-3">
      <h2 className="flex items-center gap-1.5 text-muted-foreground text-xs">
        <CatalogIconShell colorVar={colorVar} className="size-6 rounded-lg">
          <Plug size={12} />
        </CatalogIconShell>
        本机连接器{count === null ? "" : ` · ${count}`}
      </h2>
      <Link
        to={APP_PATHS.toolbox.connectors}
        className="shrink-0 text-muted-foreground text-xs hover:text-foreground"
      >
        去连接器增删启停
      </Link>
    </div>
  );
}

function FailedServer({ server }: { server: McpCatalogServer }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="truncate font-medium text-foreground text-sm">
          {server.name}
        </p>
        <span className="shrink-0 rounded-full bg-destructive/10 px-2 py-0.5 text-destructive text-xs">
          未列出
        </span>
      </div>
      <p className="mt-2 text-destructive text-xs">
        {server.error || "握手失败"}
      </p>
    </div>
  );
}

function ReadyServers({ servers }: { servers: McpCatalogServer[] }) {
  if (servers.length === 0) {
    return (
      <p className="text-muted-foreground text-xs">
        还没有已启用的本机连接器。
      </p>
    );
  }
  return (
    <div className="space-y-4">
      {servers.map((server) => (
        <div key={server.id}>
          {server.status === "failed" ? (
            <FailedServer server={server} />
          ) : (
            <>
              <h3 className="mb-2 text-muted-foreground text-xs">
                {server.name}
              </h3>
              {server.tools.length === 0 ? (
                <p className="text-muted-foreground text-xs">
                  该连接器没有可列出的工具。
                </p>
              ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(min(240px,100%),280px))] gap-3">
                  {server.tools.map((tool) => (
                    <ToolCard key={tool.name} tool={tool} accent="mcp" />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      ))}
    </div>
  );
}

/** Desktop: enabled MCP tools in the same card grid as builtins. Web: omit. */
export function McpToolsSection() {
  const { data, status, error, reload } = useMcpCatalog();

  if (status === "absent") return null;

  const toolCount = data?.reduce((n, s) => n + s.tools.length, 0) ?? null;

  return (
    <div>
      <SectionHead count={status === "ready" ? toolCount : null} />
      {status === "loading" && (
        <div className="flex items-center gap-2 py-4 text-muted-foreground text-sm">
          <Loader2 size={14} className="animate-spin" />
          正在列出本机连接器…
        </div>
      )}
      {status === "error" && (
        <div className="flex flex-col items-start gap-2">
          <p className="text-muted-foreground text-xs">
            {error || "列出本机连接器失败"}
          </p>
          <Button variant="neutral" onClick={() => reload()}>
            重试
          </Button>
        </div>
      )}
      {status === "ready" && data ? <ReadyServers servers={data} /> : null}
    </div>
  );
}
