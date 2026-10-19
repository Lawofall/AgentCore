import {
  ASSEMBLY_CARD_GRID_CLASS,
  Button,
  CatalogTile,
  Textarea,
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
import { type ImportedMcpServer, parseMcpImportJson } from "@/lib/mcpImport";
import { promptConnectorShelfCopy } from "@/lib/promptShelfTile";
import {
  ConnectorInspector,
  ConnectorStatusBadge,
  NEW_CONNECTOR_ID,
  connectorCatalogId,
  useMcpConnectors,
} from "@/pages/toolbox/ConnectorsPage";
import { ApiError } from "@/services/api";
import { createMcpServer } from "@/services/mcpServers";
import type { McpServerListItem } from "@shared/mcp-contract";
import { useState } from "react";

/** Standalone MCP route: 启用 writes the open conversation's assembly, else the star. */
export function McpRoute() {
  const plugs = useAssemblyPlugController();
  return (
    <McpPage
      plugEnabled={(id) => plugs.enabledIds.has(id)}
      onPlugToggle={plugs.locked ? undefined : plugs.toggle}
    />
  );
}

/**
 * 工具箱 MCP 栏：本机 stdio 与账号远程 HTTP 合并列表。
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
  const [busyId, setBusyId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importNote, setImportNote] = useState<string | null>(null);
  const [importSeed, setImportSeed] = useState<ImportedMcpServer | null>(null);

  const hits = mcp.servers;
  const creating = openId === NEW_CONNECTOR_ID;
  const editing = creating
    ? null
    : (mcp.servers.find((server) => connectorCatalogId(server.id) === openId) ??
      null);
  const dialogOpen = creating || editing != null;

  const closeDialog = () => {
    setOpenId(null);
    setImportSeed(null);
  };

  const applyImport = async () => {
    const parsed = parseMcpImportJson(importText);
    if (parsed.errors.length && parsed.servers.length === 0) {
      setImportNote(parsed.errors.join("；"));
      return;
    }
    if (parsed.servers.length === 1) {
      setImportSeed(parsed.servers[0]);
      setImportOpen(false);
      setImportText("");
      setImportNote(null);
      setOpenId(NEW_CONNECTOR_ID);
      return;
    }
    const notes: string[] = [...parsed.errors];
    let saved = 0;
    let skippedStdio = 0;
    for (const item of parsed.servers) {
      if (item.transport === "http") {
        try {
          await createMcpServer({
            name: item.name,
            url: item.url,
            bearer: item.bearer ?? null,
          });
          saved += 1;
        } catch (err) {
          const message =
            err instanceof ApiError && err.serverMessage
              ? err.serverMessage
              : item.name;
          notes.push(`${item.name}: ${message}`);
        }
        continue;
      }
      if (!mcp.api?.upsertServer) {
        skippedStdio += 1;
        continue;
      }
      const res = await mcp.api.upsertServer({
        id: crypto.randomUUID(),
        name: item.name,
        enabled: true,
        command: item.command,
        args: item.args,
        env: item.env,
        transport: "stdio",
      });
      if (!res.ok) notes.push(`${item.name}: ${res.error.detail}`);
      else saved += 1;
    }
    if (skippedStdio) notes.push(`跳过 ${skippedStdio} 条本机命令（仅桌面）`);
    await mcp.reload();
    if (saved > 0 && notes.length === 0) {
      setImportOpen(false);
      setImportText("");
      setImportNote(null);
      return;
    }
    setImportNote(
      saved > 0
        ? `已添加 ${saved} 条。${notes.join("；")}`
        : notes.join("；") || "没有可导入的插头",
    );
  };

  return (
    <div className="w-full" data-testid="mcp-page">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="min-w-0 text-sm font-medium text-foreground">MCP</h2>
        <div className="flex shrink-0 items-center gap-3">
          <Button
            size="md"
            variant="neutral"
            onClick={() => {
              setImportNote(null);
              setImportOpen(true);
            }}
          >
            从 JSON 导入
          </Button>
          <Button
            size="md"
            onClick={() => {
              setImportSeed(null);
              setOpenId(NEW_CONNECTOR_ID);
            }}
          >
            添加 MCP
          </Button>
        </div>
      </div>
      <McpBody
        loaded={mcp.loaded}
        error={mcp.error}
        hits={hits}
        selectedId={openId}
        onOpen={(id) => setOpenId(id)}
        plugEnabled={plugEnabled}
        onPlugToggle={onPlugToggle}
      />
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
                seed={creating ? importSeed : null}
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
      <Dialog
        open={importOpen}
        onOpenChange={(open) => {
          if (!open) {
            setImportOpen(false);
            setImportNote(null);
          }
        }}
      >
        <DialogContent
          size="lg"
          className="flex flex-col"
          data-testid="mcp-import-dialog"
        >
          <DialogHeader>
            <DialogTitle>从 JSON 导入</DialogTitle>
            <DialogDescription>
              粘贴 Cursor / Claude 的 mcpServers 或 VS Code 的
              servers。只填表，不读仓库文件。
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3 pb-5">
            {importNote ? (
              <p className="text-sm text-muted-foreground" role="alert">
                {importNote}
              </p>
            ) : null}
            <Textarea
              aria-label="MCP JSON"
              rows={10}
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
              placeholder='{"mcpServers":{"linear":{"url":"https://mcp.linear.app/mcp"}}}'
            />
            <div className="flex justify-end">
              <Button onClick={() => void applyImport()}>导入</Button>
            </div>
          </DialogBody>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function McpBody({
  loaded,
  error,
  hits,
  selectedId,
  onOpen,
  plugEnabled,
  onPlugToggle,
}: {
  loaded: boolean;
  error: string | null;
  hits: McpServerListItem[];
  selectedId: string | null;
  onOpen: (id: string) => void;
  plugEnabled?: (serverId: string) => boolean;
  onPlugToggle?: (serverId: string, enabled: boolean) => Promise<void>;
}) {
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
        还没有 MCP。
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
