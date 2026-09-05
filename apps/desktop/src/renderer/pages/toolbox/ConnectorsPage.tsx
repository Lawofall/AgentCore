import { PageContainer } from "@/components/layout/PageContainer";
import { Badge, Button, Card, Input, PageHeader } from "@/components/ui";
import { cn } from "@/lib/utils";
import { TOOLBOX_PAGE_BACK } from "@/pages/toolbox/manual/paths";
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

/**
 * 工具箱 · 集成 · 连接器：本机 stdio MCP Server 增删启停。
 * 仅 Electron（window.mcpApi）；Web stub 无 API → 诚实说明。
 */
export function ConnectorsPage() {
  const api = typeof window !== "undefined" ? window.mcpApi : undefined;
  const [servers, setServers] = useState<McpServerListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<McpServerConfig | null>(null);
  const [argsText, setArgsText] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [testNote, setTestNote] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!api?.listServers) return;
    const res = await api.listServers();
    if (!res.ok) {
      setError(res.error.detail);
      return;
    }
    setError(null);
    setServers(res.servers);
  }, [api]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (!api) {
    return (
      <PageContainer width="canvas">
        <PageHeader title="连接器" back={TOOLBOX_PAGE_BACK} />
        <p className="text-sm text-muted-foreground">
          本机 MCP 仅桌面端可用（stdio 由本机进程拉起）。当前环境无法配置本地
          MCP Server，请使用 AgentCore 桌面应用。
        </p>
      </PageContainer>
    );
  }

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
    setDraft(null);
    await reload();
  };

  const toggleEnabled = async (s: McpServerListItem) => {
    if (!api.setServerEnabled) return;
    setBusyId(s.id);
    try {
      await api.setServerEnabled(s.id, !s.enabled);
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

  return (
    <PageContainer width="canvas">
      <PageHeader
        title="连接器"
        back={TOOLBOX_PAGE_BACK}
        action={
          <Button size="md" onClick={openNew} icon={<Plus size={14} />}>
            添加 Server
          </Button>
        }
      />

      {error ? (
        <p className="mt-4 text-sm text-muted-foreground" role="alert">
          {error}
        </p>
      ) : null}
      {testNote ? (
        <p className="mt-4 text-sm text-muted-foreground">{testNote}</p>
      ) : null}

      {draft ? (
        <Card className="mt-6 flex flex-col gap-3 p-4">
          <h2 className="text-sm font-medium text-foreground">
            {draft.id ? "编辑 Server" : "新建 Server"}
          </h2>
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
          <div className="flex items-center gap-2 pt-1">
            <Button onClick={() => void saveDraft()}>保存</Button>
            <Button variant="ghost" onClick={() => setDraft(null)}>
              取消
            </Button>
          </div>
        </Card>
      ) : null}

      <ul className="mt-6 flex flex-col gap-3">
        {servers.length === 0 && !draft ? (
          <li className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            <Plug className="mx-auto mb-2 opacity-50" size={24} />
            尚未配置 MCP Server。添加一个本机 stdio 命令后即可握手。
          </li>
        ) : null}
        {servers.map((s) => (
          <li key={s.id}>
            <Card
              className={cn(
                "flex flex-col gap-3 p-4",
                !s.enabled && "opacity-70",
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-medium text-foreground">
                      {s.name}
                    </h3>
                    <Badge tone={s.enabled ? "success" : "muted"} pill>
                      {s.enabled ? "已启用" : "已停用"}
                    </Badge>
                    {s.runtimeStatus === "ready" ? (
                      <Badge tone="success" pill>
                        已握手
                      </Badge>
                    ) : null}
                    {s.runtimeStatus === "failed" ? (
                      <Badge tone="destructive" pill>
                        失败
                      </Badge>
                    ) : null}
                  </div>
                  <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                    {s.command} {s.args.join(" ")}
                  </p>
                  {s.runtimeError ? (
                    <p className="mt-1 text-xs text-destructive">
                      {s.runtimeError}
                    </p>
                  ) : null}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="neutral"
                  size="sm"
                  disabled={busyId === s.id}
                  onClick={() => void toggleEnabled(s)}
                >
                  {s.enabled ? "停用" : "启用"}
                </Button>
                <Button
                  variant="neutral"
                  size="sm"
                  disabled={busyId === s.id}
                  onClick={() => openEdit(s)}
                >
                  编辑
                </Button>
                <Button
                  variant="neutral"
                  size="sm"
                  disabled={busyId === s.id}
                  icon={<RefreshCw size={14} />}
                  onClick={() => void test(s.id)}
                >
                  测试握手
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busyId === s.id}
                  icon={<Trash2 size={14} />}
                  onClick={() => void remove(s.id)}
                >
                  删除
                </Button>
              </div>
            </Card>
          </li>
        ))}
      </ul>
    </PageContainer>
  );
}
