import {
  Badge,
  Button,
  CATALOG_GRID_CLASS,
  CatalogTile,
  Input,
} from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { artifactColorVar } from "@/lib/catalogColors";
import type { McpServerConfig, McpServerListItem } from "@shared/mcp-contract";
import { Plug, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

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

function statusBadge(server: McpServerListItem) {
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

/**
 * 本机插头：与出厂工具同一套图鉴卡。点卡打开配置 Dialog。
 * 仅 Electron（window.mcpApi）；Web 不渲染。
 */
export function ConnectorsPage() {
  const api = typeof window !== "undefined" ? window.mcpApi : undefined;
  const [servers, setServers] = useState<McpServerListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<McpServerConfig | null>(null);
  const [argsText, setArgsText] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [testNote, setTestNote] = useState<string | null>(null);
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

  if (!api) return null;

  const openNew = () => {
    setDraft(emptyDraft());
    setArgsText("");
    setTestNote(null);
  };

  const openEdit = (s: McpServerListItem) => {
    setDraft({
      ...s,
      args: [...s.args],
      env: s.env ? { ...s.env } : undefined,
    });
    setArgsText(s.args.join(" "));
    setTestNote(null);
  };

  const closeDraft = () => {
    setDraft(null);
    setTestNote(null);
  };

  const saveDraft = async () => {
    if (!draft || !api.upsertServer) return;
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
    closeDraft();
    await reload();
  };

  const toggleEnabled = async (s: McpServerConfig) => {
    if (!s.id || !api.setServerEnabled) return;
    setBusyId(s.id);
    try {
      await api.setServerEnabled(s.id, !s.enabled);
      setDraft({ ...s, enabled: !s.enabled });
      await reload();
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (id: string) => {
    if (!api.removeServer) return;
    setBusyId(id);
    try {
      await api.removeServer(id);
      closeDraft();
      await reload();
    } finally {
      setBusyId(null);
    }
  };

  const test = async (id: string) => {
    if (!api.testServer) return;
    setBusyId(id);
    setTestNote(null);
    try {
      const res = await api.testServer(id);
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
      await reload();
    } finally {
      setBusyId(null);
    }
  };

  const colorVar = artifactColorVar("connectors");
  const editing = Boolean(draft?.id);

  return (
    <div className="space-y-3">
      {error ? (
        <p className="text-sm text-muted-foreground" role="alert">
          {error}
        </p>
      ) : null}
      {loaded ? (
        <div className={CATALOG_GRID_CLASS}>
          {servers.map((s) => (
            <CatalogTile
              key={s.id}
              icon={<Plug size={18} />}
              colorVar={colorVar}
              title={s.name}
              subtitle={`${s.command} ${s.args.join(" ")}`.trim() || undefined}
              description={s.runtimeError || undefined}
              muted={!s.enabled}
              accessory={statusBadge(s)}
              onClick={() => openEdit(s)}
            />
          ))}
          <CatalogTile
            icon={<Plus size={18} />}
            colorVar={colorVar}
            title="添加连接器"
            onClick={openNew}
          />
        </div>
      ) : null}

      <Dialog
        open={draft !== null}
        onOpenChange={(open) => !open && closeDraft()}
      >
        <DialogContent className="flex max-h-[min(80vh,36rem)] flex-col">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑连接器" : "新建连接器"}</DialogTitle>
          </DialogHeader>
          {draft ? (
            <div className="min-h-0 space-y-3 overflow-y-auto px-5">
              {editing ? (
                <div className="flex items-center gap-2">
                  <Switch
                    checked={draft.enabled}
                    disabled={busyId === draft.id}
                    onCheckedChange={() => void toggleEnabled(draft)}
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
                  onChange={(e) =>
                    setDraft({ ...draft, command: e.target.value })
                  }
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
                    onClick={() => void test(draft.id)}
                  >
                    测试握手
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={busyId === draft.id}
                    icon={<Trash2 size={14} />}
                    onClick={() => void remove(draft.id)}
                  >
                    删除
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="ghost" onClick={closeDraft}>
              取消
            </Button>
            <Button onClick={() => void saveDraft()}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
