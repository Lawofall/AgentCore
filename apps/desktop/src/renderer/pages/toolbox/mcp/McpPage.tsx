import {
  ASSEMBLY_CARD_GRID_CLASS,
  Button,
  CatalogTile,
  SearchField,
} from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAssemblyPlugController } from "@/lib/assemblyMcp";
import { promptConnectorShelfCopy } from "@/lib/promptShelfTile";
import {
  ConnectorInspector,
  ConnectorStatusBadge,
  NEW_CONNECTOR_ID,
  connectorCatalogId,
  useMcpConnectors,
} from "@/pages/toolbox/ConnectorsPage";
import type { McpServerListItem } from "@shared/mcp-contract";
import { useState } from "react";

function matchServer(query: string, server: McpServerListItem): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    server.name.toLowerCase().includes(q) ||
    (server.runtimeError ?? "").toLowerCase().includes(q)
  );
}

/** Standalone MCP route: 启用 writes the open conversation's assembly, else the star. */
export function McpRoute() {
  const plugs = useAssemblyPlugController();
  return (
    <McpPage
      plugEnabled={(id) => plugs.enabledIds.has(id)}
      onPlugToggle={plugs.toggle}
    />
  );
}

/**
 * 工具箱 MCP 栏：本机 stdio Server 列表。没有 mcpApi 时不假装已接上。
 * 不把插头报出的动作再铺一层。启用写在装配的插头名单上。
 */
export function McpPage({
  plugEnabled,
  onPlugToggle,
}: {
  plugEnabled?: (serverId: string) => boolean;
  onPlugToggle?: (serverId: string, enabled: boolean) => Promise<void>;
}) {
  const mcp = useMcpConnectors();
  const [query, setQuery] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const hits = mcp.servers.filter((server) => matchServer(query, server));
  const creating = openId === NEW_CONNECTOR_ID;
  const editing = creating
    ? null
    : (mcp.servers.find((server) => connectorCatalogId(server.id) === openId) ??
      null);
  const dialogOpen = Boolean(mcp.api) && (creating || editing != null);

  const closeDialog = () => {
    setOpenId(null);
  };

  const addActions = mcp.api ? (
    <>
      <SearchField
        aria-label="搜 MCP"
        placeholder="搜 MCP"
        value={query}
        onValueChange={setQuery}
        className="w-52"
      />
      <Button size="md" onClick={() => setOpenId(NEW_CONNECTOR_ID)}>
        添加 MCP
      </Button>
    </>
  ) : null;

  return (
    <div className="w-full" data-testid="mcp-page">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="min-w-0 text-sm font-medium text-foreground">MCP</h2>
        {addActions ? (
          <div className="flex shrink-0 items-center gap-3">{addActions}</div>
        ) : null}
      </div>
      <McpBody
        api={mcp.api}
        loaded={mcp.loaded}
        error={mcp.error}
        query={query}
        hits={hits}
        selectedId={openId}
        onOpen={(id) => setOpenId(id)}
        plugEnabled={plugEnabled}
        onPlugToggle={onPlugToggle}
      />
      {mcp.api ? (
        <Dialog
          open={dialogOpen}
          onOpenChange={(open) => {
            if (!open) closeDialog();
          }}
        >
          <DialogContent
            size="lg"
            className="flex max-h-[min(80vh,36rem)] flex-col"
            data-testid="mcp-dialog"
          >
            <DialogHeader className="shrink-0">
              <div className="flex flex-wrap items-center gap-2">
                <DialogTitle>{creating ? "新建 MCP" : "编辑 MCP"}</DialogTitle>
                {editing ? (
                  <ConnectorStatusBadge
                    server={
                      plugEnabled
                        ? { ...editing, enabled: plugEnabled(editing.id) }
                        : editing
                    }
                  />
                ) : null}
              </div>
              <DialogDescription className="sr-only">
                {creating ? "新建 MCP" : (editing?.name ?? "编辑 MCP")}
              </DialogDescription>
            </DialogHeader>
            <DialogBody className="flex min-h-0 flex-1 flex-col pb-5">
              {dialogOpen ? (
                <ConnectorInspector
                  key={openId ?? NEW_CONNECTOR_ID}
                  server={editing}
                  api={mcp.api}
                  busyId={busyId}
                  onBusy={setBusyId}
                  onCloseNew={closeDialog}
                  onSaved={mcp.reload}
                  hideChrome
                  assemblyEnabled={
                    editing && plugEnabled ? plugEnabled(editing.id) : undefined
                  }
                  onAssemblyToggle={
                    editing && onPlugToggle
                      ? (enabled) => onPlugToggle(editing.id, enabled)
                      : undefined
                  }
                />
              ) : null}
            </DialogBody>
          </DialogContent>
        </Dialog>
      ) : null}
    </div>
  );
}

function McpBody({
  api,
  loaded,
  error,
  query,
  hits,
  selectedId,
  onOpen,
  plugEnabled,
  onPlugToggle,
}: {
  api: Window["mcpApi"];
  loaded: boolean;
  error: string | null;
  query: string;
  hits: McpServerListItem[];
  selectedId: string | null;
  onOpen: (id: string) => void;
  plugEnabled?: (serverId: string) => boolean;
  onPlugToggle?: (serverId: string, enabled: boolean) => Promise<void>;
}) {
  if (!api) {
    return (
      <p className="text-sm text-muted-foreground">MCP 只在桌面本机可用。</p>
    );
  }
  if (!loaded) return null;
  if (error) {
    return (
      <p className="text-sm text-muted-foreground" role="alert">
        {error}
      </p>
    );
  }
  if (hits.length === 0) {
    return (
      <div className="flex min-h-[4.5rem] items-center justify-center rounded-xl border border-dashed border-border px-4 text-center text-sm text-muted-foreground">
        {query.trim() ? "没有匹配的 MCP。" : "还没有 MCP。"}
      </div>
    );
  }
  return (
    <div className={ASSEMBLY_CARD_GRID_CLASS} data-testid="mcp-list">
      {hits.map((server) => {
        const id = connectorCatalogId(server.id);
        const shown = plugEnabled
          ? { ...server, enabled: plugEnabled(server.id) }
          : server;
        const copy = promptConnectorShelfCopy({
          label: server.name,
          runtimeError: server.runtimeError,
        });
        return (
          <CatalogTile
            key={id}
            density="compact"
            title={copy.title}
            description={copy.description || undefined}
            onClick={() => onOpen(id)}
            className={
              selectedId === id ? "border-foreground/20 bg-muted/50" : undefined
            }
            accessory={
              <>
                <ConnectorStatusBadge server={shown} />
                {onPlugToggle ? (
                  <Switch
                    checked={shown.enabled}
                    label={`启用${shown.name}`}
                    onCheckedChange={(on) => void onPlugToggle(server.id, on)}
                  />
                ) : null}
              </>
            }
          />
        );
      })}
    </div>
  );
}
