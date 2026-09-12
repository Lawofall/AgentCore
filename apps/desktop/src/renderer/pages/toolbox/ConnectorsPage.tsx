import { Badge, Button, Input } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import type { McpServerConfig, McpServerListItem } from "@shared/mcp-contract";
import { RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

export const NEW_CONNECTOR_ID = "connector:new";

export function connectorCatalogId(id: string): string {
  return `connector:${id}`;
}

function emptyDraft(): McpServerConfig {
  return {
    id: "",
    name: "",
    enabled: true,
    command: "",
    args: [],
    env: undefined,
  };
}

export function connectorStatusLabel(server: McpServerListItem): string {
  if (!server.enabled) return "未启用";
  if (server.runtimeStatus === "ready") return "已握手";
  if (server.runtimeStatus === "failed") return "失败";
  return "";
}

export function ConnectorStatusBadge({
  server,
}: { server: McpServerListItem }) {
  if (!server.enabled) {
    return (
      <Badge tone="muted" pill>
        未启用
      </Badge>
    );
  }
  if (server.runtimeStatus === "ready") {
    return (
      <Badge tone="success" pill>
        已握手
      </Badge>
    );
  }
  if (server.runtimeStatus === "failed") {
    return (
      <Badge tone="destructive" pill>
        失败
      </Badge>
    );
  }
  return null;
}

export function useMcpConnectors() {
  const api = typeof window !== "undefined" ? window.mcpApi : undefined;
  const [servers, setServers] = useState<McpServerListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(!api?.listServers);

  const reload = useCallback(async () => {
    if (!api?.listServers) return;
    const res = await api.listServers();
    if (!res.ok) {
      setError(res.error.detail);
      setLoaded(true);
      return;
    }
    setError(null);
    setServers(res.servers);
    setLoaded(true);
  }, [api]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { api, servers, error, loaded, reload, setError };
}

/**
 * 本机插头配置：提示词目录读卡。仅 Electron（window.mcpApi）。
 */
export function ConnectorInspector({
  server,
  api,
  busyId,
  onBusy,
  onCloseNew,
  onSaved,
  hideChrome = false,
}: {
  server: McpServerListItem | null;
  api: NonNullable<Window["mcpApi"]>;
  busyId: string | null;
  onBusy: (id: string | null) => void;
  onCloseNew?: () => void;
  onSaved: () => Promise<void>;
  hideChrome?: boolean;
}) {
  const editing = Boolean(server?.id);
  const [draft, setDraft] = useState<McpServerConfig>(() =>
    server
      ? {
          ...server,
          args: [...server.args],
          env: server.env ? { ...server.env } : undefined,
        }
      : emptyDraft(),
  );
  const [argsText, setArgsText] = useState(() =>
    server ? server.args.join(" ") : "",
  );
  const [testNote, setTestNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (server) {
      setDraft({
        ...server,
        args: [...server.args],
        env: server.env ? { ...server.env } : undefined,
      });
      setArgsText(server.args.join(" "));
    } else {
      setDraft(emptyDraft());
      setArgsText("");
    }
    setTestNote(null);
    setError(null);
  }, [server]);

  const saveDraft = async () => {
    if (!api.upsertServer) return;
    const args = argsText.trim().split(/\s+/).filter(Boolean);
    const payload: McpServerConfig = {
      ...draft,
      id: draft.id || crypto.randomUUID(),
      args,
    };
    const res = await api.upsertServer(payload);
    if (!res.ok) {
      setError(res.error.detail);
      return;
    }
    await onSaved();
    if (!editing) onCloseNew?.();
  };

  const toggleEnabled = async () => {
    if (!draft.id || !api.setServerEnabled) return;
    onBusy(draft.id);
    try {
      await api.setServerEnabled(draft.id, !draft.enabled);
      setDraft({ ...draft, enabled: !draft.enabled });
      await onSaved();
    } finally {
      onBusy(null);
    }
  };

  const remove = async () => {
    if (!draft.id || !api.removeServer) return;
    onBusy(draft.id);
    try {
      await api.removeServer(draft.id);
      await onSaved();
      onCloseNew?.();
    } finally {
      onBusy(null);
    }
  };

  const test = async () => {
    if (!draft.id || !api.testServer) return;
    onBusy(draft.id);
    setTestNote(null);
    try {
      const res = await api.testServer(draft.id);
      if (!res.ok) {
        setTestNote(res.error.detail);
        return;
      }
      if (res.status === "ready") {
        setTestNote(
          `握手成功，发现 ${res.tools.length} 个工具：${
            res.tools.map((t) => t.name).join(", ") || "（无）"
          }`,
        );
      } else {
        setTestNote(`握手失败：${res.error || "unknown"}`);
      }
      await onSaved();
    } finally {
      onBusy(null);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {hideChrome ? null : (
        <header className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border px-3">
          <h2 className="font-medium text-foreground text-sm">
            {editing ? "编辑连接器" : "新建连接器"}
          </h2>
          {server ? <ConnectorStatusBadge server={server} /> : null}
        </header>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        <div className="space-y-3">
          {error ? (
            <p className="text-sm text-muted-foreground" role="alert">
              {error}
            </p>
          ) : null}
          {editing ? (
            <div className="flex items-center gap-2">
              <Switch
                checked={draft.enabled}
                disabled={busyId === draft.id}
                onCheckedChange={() => void toggleEnabled()}
                label="启用"
              />
              <span className="text-xs text-muted-foreground">启用</span>
            </div>
          ) : null}
          <label
            className="flex flex-col gap-1 text-xs text-muted-foreground"
            htmlFor="mcp-draft-name"
          >
            显示名
            <Input
              id="mcp-draft-name"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="例如：Filesystem"
            />
          </label>
          <label
            className="flex flex-col gap-1 text-xs text-muted-foreground"
            htmlFor="mcp-draft-command"
          >
            命令
            <Input
              id="mcp-draft-command"
              value={draft.command}
              onChange={(e) => setDraft({ ...draft, command: e.target.value })}
              placeholder="例如：npx"
            />
          </label>
          <label
            className="flex flex-col gap-1 text-xs text-muted-foreground"
            htmlFor="mcp-draft-args"
          >
            参数（空格分隔）
            <Input
              id="mcp-draft-args"
              value={argsText}
              onChange={(e) => setArgsText(e.target.value)}
              placeholder="例如：-y @modelcontextprotocol/server-everything"
            />
          </label>
          {testNote ? (
            <p className="text-xs text-muted-foreground">{testNote}</p>
          ) : null}
          {editing ? (
            <div className="flex flex-wrap gap-2">
              <Button
                variant="neutral"
                size="sm"
                disabled={busyId === draft.id}
                icon={<RefreshCw size={14} />}
                onClick={() => void test()}
              >
                测试握手
              </Button>
              <Button
                variant="danger"
                size="sm"
                disabled={busyId === draft.id}
                icon={<Trash2 size={14} />}
                onClick={() => void remove()}
              >
                删除
              </Button>
            </div>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button onClick={() => void saveDraft()}>保存</Button>
          </div>
        </div>
      </div>
    </div>
  );
}
