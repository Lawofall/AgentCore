import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Card, SectionHeader } from "@/components/ui/Page";
import { Spinner } from "@/components/ui/Spinner";
import {
  EmptyState,
  ErrorState,
  Refreshing,
  TableSkeleton,
} from "@/components/ui/States";
import { TableFrame, TableRow, THead, Td, Th } from "@/components/ui/Table";
import { fmtTime } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import {
  type CreatePlatformCredentialRequest,
  type PlatformCredentialListResponse,
  type PlatformCredentialView,
  type ToolSurfaceLimits,
  type UpdatePlatformCredentialRequest,
  clearPlatformCredentialRuntime,
  createPlatformCredential,
  deletePlatformCredential,
  listPlatformCredentials,
  updatePlatformCredential,
} from "@/services/adminPlatformCredentials";
import { Info, Pencil, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useId, useState } from "react";
import { toast } from "sonner";

const GO_BASE_URL = "https://opencode.ai/zen/go/v1";

const OPS_CHECKLIST = [
  "该工作区已完成 DeepSeek 中国区托管 opt-in（未同意会回 RegionError；新号漏做会拖垮全体用户）",
  `用该 key 实测 POST ${GO_BASE_URL}/chat/completions + deepseek-v4-flash 通过`,
  "Base URL 配对 Go 端点（/zen/go/v1），不是 Zen（/zen/v1）——打错会静默错账",
  "该 workspace 已订阅 Go（一个 workspace 仅一名成员可订阅）",
  "可选：控制台开 Use balance，Go 窗口打满后回落 Zen 余额",
];

const FALLBACK_HINT: Record<
  PlatformCredentialListResponse["fallback"],
  string
> = {
  pool: "选钥 fill-first 并会话粘号；429 / 余额不足 / 403 换号，401 封禁不换。封禁/冷却/耗尽可解封，不必删行。",
  env: "当前无启用成员，平台调用回落 env 里的 PLATFORM_API_KEY（与改池前行为一致）。",
  none: "池中无启用成员，且未配置 PLATFORM_API_KEY。平台代付不可用。",
};

type RuntimeStatus = NonNullable<PlatformCredentialView["status"]>;

const RUNTIME_LABEL: Record<RuntimeStatus, string> = {
  healthy: "健康",
  cooling: "冷却中",
  exhausted: "额度耗尽",
  blocked: "已封禁",
};

function runtimeStatus(row: PlatformCredentialView): RuntimeStatus {
  return row.status ?? "healthy";
}

function runtimeTone(
  status: RuntimeStatus,
): "success" | "warning" | "destructive" {
  if (status === "blocked") return "destructive";
  if (status === "cooling" || status === "exhausted") return "warning";
  return "success";
}

function canClearRuntime(status: RuntimeStatus): boolean {
  return status === "blocked" || status === "cooling" || status === "exhausted";
}

function formatToolSurface(
  limits: PlatformCredentialView["tool_surface_limits"],
): string {
  const parts: string[] = [];
  if (limits?.max_tools != null) parts.push(`条数≤${limits.max_tools}`);
  if (limits?.max_properties_total != null) {
    parts.push(`属性合计≤${limits.max_properties_total}`);
  }
  if (limits?.max_properties_per_tool != null) {
    parts.push(`每工具属性≤${limits.max_properties_per_tool}`);
  }
  return parts.length > 0 ? parts.join(" · ") : "不限";
}

function optionalLimitField(raw: string): number | undefined | "invalid" {
  const text = raw.trim();
  if (!text) return undefined;
  const n = Number(text);
  if (!Number.isInteger(n) || n < 0) return "invalid";
  return n;
}

function limitsPayload(
  maxTools: string,
  maxPropertiesTotal: string,
  maxPropertiesPerTool: string,
): { ok: true; value: ToolSurfaceLimits } | { ok: false } {
  const max_tools = optionalLimitField(maxTools);
  const max_properties_total = optionalLimitField(maxPropertiesTotal);
  const max_properties_per_tool = optionalLimitField(maxPropertiesPerTool);
  if (
    max_tools === "invalid" ||
    max_properties_total === "invalid" ||
    max_properties_per_tool === "invalid"
  ) {
    return { ok: false };
  }
  return {
    ok: true,
    value: {
      ...(max_tools !== undefined ? { max_tools } : {}),
      ...(max_properties_total !== undefined ? { max_properties_total } : {}),
      ...(max_properties_per_tool !== undefined
        ? { max_properties_per_tool }
        : {}),
    },
  };
}

export function PlatformCredentialsCard({
  refreshEpoch = 0,
}: {
  /** Parent bumps this to reload the pool (page-header refresh). */
  refreshEpoch?: number;
}) {
  const [data, setData] = useState<PlatformCredentialListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<"create" | PlatformCredentialView | null>(
    null,
  );
  const [pendingDelete, setPendingDelete] =
    useState<PlatformCredentialView | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await listPlatformCredentials());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshEpoch]);

  const onSaved = (row: PlatformCredentialView) => {
    setData((prev) => {
      if (!prev) return prev;
      const idx = prev.data.findIndex((r) => r.id === row.id);
      const next =
        idx >= 0
          ? prev.data.map((r) => (r.id === row.id ? row : r))
          : [...prev.data, row];
      const hasEnabled = next.some((r) => r.enabled);
      return {
        data: next,
        fallback: hasEnabled ? "pool" : prev.fallback === "none" ? "none" : "env",
      };
    });
    void load();
  };

  return (
    <Card>
      <SectionHeader
        title="平台额度账号"
        description="池成员可热更；空池回落 env 单 key。选钥 fill-first 并会话粘号，429/余额不足/403 自动换号。"
        action={
          <Button
            size="sm"
            onClick={() => setEditor("create")}
            aria-label="新增账号"
          >
            <Plus size={14} />
            新增
          </Button>
        }
      />
      <div className="flex flex-col gap-4 p-5">
        <div className="flex items-start gap-2.5 rounded-xl border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
          <Info size={16} className="mt-0.5 shrink-0 text-primary" />
          <div className="flex flex-col gap-2">
            <span>
              每加一个账号必须先做完下列运维前置，跳步会让新号一进池就拖垮全体用户：
            </span>
            <ol className="list-decimal space-y-1 pl-5">
              {OPS_CHECKLIST.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ol>
          </div>
        </div>

        {error && !data ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : !data && loading ? (
          <TableSkeleton rows={3} columns={9} />
        ) : data && data.data.length === 0 ? (
          <EmptyState
            title="还没有池成员"
            description={FALLBACK_HINT[data.fallback]}
          />
        ) : data ? (
          <Refreshing active={loading} className="flex flex-col gap-3">
            <p className="text-muted-foreground text-sm">
              {FALLBACK_HINT[data.fallback]}
            </p>
            <TableFrame minWidth={1240}>
              <THead>
                <Th>名称</Th>
                <Th>标识</Th>
                <Th>Base URL</Th>
                <Th>订阅日</Th>
                <Th>Key</Th>
                <Th>工具面</Th>
                <Th>状态</Th>
                <Th>运行态</Th>
                <Th>恢复时间</Th>
                <Th align="right">操作</Th>
              </THead>
              <tbody>
                {data.data.map((row) => {
                  const status = runtimeStatus(row);
                  return (
                  <TableRow key={row.id}>
                    <Td className="font-medium">
                      <div className="flex flex-col gap-0.5">
                        <span>{row.label}</span>
                        <div className="flex flex-wrap gap-1">
                          {row.picked ? (
                            <Badge tone="success">当前选中</Badge>
                          ) : null}
                          {row.same_as_env ? (
                            <Badge tone="warning">与 env 同 key</Badge>
                          ) : null}
                        </div>
                      </div>
                    </Td>
                    <Td>
                      <code className="text-xs tabular-nums">{row.id}</code>
                    </Td>
                    <Td className="max-w-[220px] truncate" title={row.base_url}>
                      {row.base_url}
                    </Td>
                    <Td className="tabular-nums">{row.subscription_day}</Td>
                    <Td className="tabular-nums">{row.masked_key ?? "••••"}</Td>
                    <Td
                      className="max-w-[220px] truncate text-muted-foreground"
                      title={formatToolSurface(row.tool_surface_limits)}
                    >
                      {formatToolSurface(row.tool_surface_limits)}
                    </Td>
                    <Td>
                      <Badge tone={row.enabled ? "success" : "warning"}>
                        {row.enabled ? "启用" : "已禁用"}
                      </Badge>
                    </Td>
                    <Td>
                      <div className="flex flex-col gap-0.5">
                        <Badge tone={runtimeTone(status)}>
                          {RUNTIME_LABEL[status]}
                        </Badge>
                        {row.limit_name ? (
                          <span className="text-muted-foreground text-xs">
                            {row.limit_name}
                          </span>
                        ) : null}
                        {row.source ? (
                          <span className="text-muted-foreground text-xs">
                            {row.source}
                          </span>
                        ) : null}
                      </div>
                    </Td>
                    <Td className="tabular-nums text-muted-foreground">
                      {row.recovery_at ? fmtTime(row.recovery_at) : "—"}
                    </Td>
                    <Td align="right">
                      <div className="flex justify-end gap-1">
                        {canClearRuntime(status) ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              void clearRuntime(row).then((updated) => {
                                if (updated) onSaved(updated);
                              })
                            }
                          >
                            解封
                          </Button>
                        ) : null}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            void toggleEnabled(row).then((updated) => {
                              if (updated) onSaved(updated);
                            })
                          }
                        >
                          {row.enabled ? "禁用" : "启用"}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={`编辑 ${row.label}`}
                          onClick={() => setEditor(row)}
                        >
                          <Pencil size={14} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={`删除 ${row.label}`}
                          onClick={() => setPendingDelete(row)}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                    </Td>
                  </TableRow>
                  );
                })}
              </tbody>
            </TableFrame>
          </Refreshing>
        ) : null}
      </div>

      {editor && (
        <CredentialEditorDialog
          initial={editor === "create" ? null : editor}
          onClose={() => setEditor(null)}
          onSaved={(row) => {
            setEditor(null);
            onSaved(row);
          }}
        />
      )}
      {pendingDelete && (
        <DeleteCredentialDialog
          row={pendingDelete}
          onClose={() => setPendingDelete(null)}
          onDeleted={() => {
            setPendingDelete(null);
            void load();
          }}
        />
      )}
    </Card>
  );
}

async function toggleEnabled(
  row: PlatformCredentialView,
): Promise<PlatformCredentialView | null> {
  try {
    const updated = await updatePlatformCredential(row.id, {
      enabled: !row.enabled,
    });
    toast.success(updated.enabled ? "已启用" : "已禁用，选钥不再使用该号");
    return updated;
  } catch (err) {
    toast.error(errorMessage(err));
    return null;
  }
}

async function clearRuntime(
  row: PlatformCredentialView,
): Promise<PlatformCredentialView | null> {
  try {
    const updated = await clearPlatformCredentialRuntime(row.id);
    toast.success("已解封，选钥可再次使用该号");
    return updated;
  } catch (err) {
    toast.error(errorMessage(err));
    return null;
  }
}

function CredentialEditorDialog({
  initial,
  onClose,
  onSaved,
}: {
  initial: PlatformCredentialView | null;
  onClose: () => void;
  onSaved: (row: PlatformCredentialView) => void;
}) {
  const formId = useId();
  const creating = initial == null;
  const [label, setLabel] = useState(initial?.label ?? "");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? GO_BASE_URL);
  const [subscriptionDay, setSubscriptionDay] = useState(
    String(initial?.subscription_day ?? 1),
  );
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [maxTools, setMaxTools] = useState(
    initial?.tool_surface_limits?.max_tools != null
      ? String(initial.tool_surface_limits.max_tools)
      : "",
  );
  const [maxPropertiesTotal, setMaxPropertiesTotal] = useState(
    initial?.tool_surface_limits?.max_properties_total != null
      ? String(initial.tool_surface_limits.max_properties_total)
      : "",
  );
  const [maxPropertiesPerTool, setMaxPropertiesPerTool] = useState(
    initial?.tool_surface_limits?.max_properties_per_tool != null
      ? String(initial.tool_surface_limits.max_properties_per_tool)
      : "",
  );
  const [saving, setSaving] = useState(false);

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    if (saving) return;
    const day = Number(subscriptionDay);
    if (!Number.isInteger(day) || day < 1 || day > 31) {
      toast.error("订阅日须为 1–31");
      return;
    }
    if (creating && !apiKey.trim()) {
      toast.error("API Key 不能为空");
      return;
    }
    const limits = limitsPayload(maxTools, maxPropertiesTotal, maxPropertiesPerTool);
    if (!limits.ok) {
      toast.error("工具面上限须为空（不限）或非负整数");
      return;
    }
    setSaving(true);
    try {
      if (creating) {
        const body: CreatePlatformCredentialRequest = {
          label: label.trim(),
          api_key: apiKey.trim(),
          base_url: baseUrl.trim(),
          subscription_day: day,
          enabled,
          tool_surface_limits: limits.value,
        };
        const row = await createPlatformCredential(body);
        toast.success("已加入账号池");
        onSaved(row);
      } else {
        const patch: UpdatePlatformCredentialRequest = {
          label: label.trim(),
          base_url: baseUrl.trim(),
          subscription_day: day,
          enabled,
          tool_surface_limits: limits.value,
        };
        if (apiKey.trim()) patch.api_key = apiKey.trim();
        const row = await updatePlatformCredential(initial.id, patch);
        toast.success("已保存");
        onSaved(row);
      }
    } catch (err) {
      toast.error(errorMessage(err));
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      busy={saving}
      title={creating ? "新增平台账号" : "编辑平台账号"}
      description="Key 与 Base URL 必须成对绑定。明文 Key 只在提交时发送，不会再回显。"
      footer={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button size="sm" type="submit" form={formId} disabled={saving}>
            {saving && <Spinner />}
            保存
          </Button>
        </>
      }
    >
      <form id={formId} className="flex flex-col gap-3" onSubmit={(e) => void handleSave(e)}>
        <Field label="名称">
          <Input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            required
            maxLength={100}
            placeholder="Go 号 2 · 8 月购"
          />
        </Field>
        <Field label={creating ? "API Key" : "API Key（留空则不改）"}>
          <Input
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            required={creating}
            maxLength={400}
            placeholder={creating ? "" : "••••"}
          />
        </Field>
        <Field label="Base URL">
          <Input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            required
            maxLength={500}
            placeholder={GO_BASE_URL}
          />
        </Field>
        <Field label="订阅日（UTC，1–31）">
          <Input
            type="number"
            min={1}
            max={31}
            value={subscriptionDay}
            onChange={(e) => setSubscriptionDay(e.target.value)}
            required
          />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          启用（取消勾选 = 立刻从选钥中摘除）
        </label>
        <p className="text-muted-foreground text-xs">
          上游工具面：空=不限。超限时该号装不下当前开场表，不会发给上游，也不会自动裁剪。
        </p>
        <Field label="工具条数上限（空=不限）">
          <Input
            type="number"
            min={0}
            value={maxTools}
            onChange={(e) => setMaxTools(e.target.value)}
            placeholder="空=不限"
          />
        </Field>
        <Field label="属性合计上限（空=不限）">
          <Input
            type="number"
            min={0}
            value={maxPropertiesTotal}
            onChange={(e) => setMaxPropertiesTotal(e.target.value)}
            placeholder="空=不限"
          />
        </Field>
        <Field label="每工具属性数上限（空=不限）">
          <Input
            type="number"
            min={0}
            value={maxPropertiesPerTool}
            onChange={(e) => setMaxPropertiesPerTool(e.target.value)}
            placeholder="空=不限"
          />
        </Field>
      </form>
    </Dialog>
  );
}

function DeleteCredentialDialog({
  row,
  onClose,
  onDeleted,
}: {
  row: PlatformCredentialView;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const handleDelete = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await deletePlatformCredential(row.id);
      toast.success("已删除");
      onDeleted();
    } catch (err) {
      toast.error(errorMessage(err));
      setSaving(false);
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      busy={saving}
      title="删除平台账号"
      description="历史 cost_calls 仍保留该号的标识；封号请优先禁用而不是删除。"
      footer={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => void handleDelete()}
            disabled={saving}
          >
            {saving && <Spinner />}
            确认删除
          </Button>
        </>
      }
    >
      <p className="text-muted-foreground text-sm">
        将删除{" "}
        <span className="font-medium text-foreground">{row.label}</span>
        。选钥不再使用该号。
      </p>
    </Dialog>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}
