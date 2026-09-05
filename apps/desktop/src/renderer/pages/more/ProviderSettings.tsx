import {
  ModelKeyForm,
  modelConfigApiErrorMessage,
} from "@/components/llm/ModelKeyForm";
import { ToolsCapabilityBadge } from "@/components/llm/ToolsCapabilityBadge";
import { SettingsAsync } from "@/components/settings";
import { Button, Card, ConfirmDialog, PageHeader } from "@/components/ui";
import { useLlmProviders } from "@/hooks/useLlmProviders";
import {
  llmModelProfileKeys,
  llmProviderKeys,
  modelKeys,
} from "@/lib/queryKeys";
import {
  type LlmProviderView,
  deleteLlmProvider,
  testLlmProvider,
} from "@/services/llmProviders";
import { useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Loader2,
  Plus,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useState } from "react";

/**
 * 服务商 (/more/providers) — BYOK 列表 / 表单 / 测连 + 安全说明。
 * 页头只留标题；准入走空态，选用组合在「设置 · 模型」。
 */
export function ProviderSettings() {
  const { data: response, isLoading, isError, error } = useLlmProviders();
  const queryClient = useQueryClient();

  const [form, setForm] = useState<
    { mode: "add" } | { mode: "edit"; provider: LlmProviderView } | null
  >(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testMessage, setTestMessage] = useState<Record<string, string | null>>(
    {},
  );
  const [cardError, setCardError] = useState<Record<string, string | null>>({});
  const [pendingDelete, setPendingDelete] = useState<LlmProviderView | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: llmProviderKeys.list });
    void queryClient.invalidateQueries({ queryKey: modelKeys.catalog });
    void queryClient.invalidateQueries({ queryKey: llmModelProfileKeys.list });
  };

  const runTest = async (providerId: string) => {
    setTestingId(providerId);
    setCardError((s) => ({ ...s, [providerId]: null }));
    try {
      const view = await testLlmProvider(providerId);
      setTestMessage((s) => ({ ...s, [providerId]: view.message ?? null }));
    } catch (e) {
      setCardError((s) => ({
        ...s,
        [providerId]: modelConfigApiErrorMessage(e, "测试失败，请重试"),
      }));
    } finally {
      setTestingId(null);
      refresh();
    }
  };

  const onSavedProvider = (view: LlmProviderView) => {
    setForm(null);
    refresh();
    void runTest(view.id);
  };

  /** 删除后果分两档：还有其他服务商或平台额度兜底 = 槽位自动回落；否则断供。 */
  const deleteConsequence = (): string => {
    const remaining = (response?.providers.length ?? 1) - 1;
    return remaining > 0 || response?.platform_available
      ? "组合槽位会自动回落到其他服务商或平台额度，不会中断对话。"
      : "这是唯一的服务商，删除后将无法发起对话，直到重新接入。";
  };

  const removeProvider = async (provider: LlmProviderView) => {
    setDeleting(true);
    setCardError((s) => ({ ...s, [provider.id]: null }));
    try {
      await deleteLlmProvider(provider.id);
      if (form?.mode === "edit" && form.provider.id === provider.id) {
        setForm(null);
      }
      refresh();
    } catch (e) {
      setCardError((s) => ({
        ...s,
        [provider.id]: modelConfigApiErrorMessage(e, "删除失败，请重试"),
      }));
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  };

  const providers = response?.providers ?? [];

  return (
    <div>
      <PageHeader title="服务商" />

      {isLoading || isError || !response ? (
        <SettingsAsync
          className="mt-6"
          loading={isLoading}
          error={
            isLoading
              ? undefined
              : modelConfigApiErrorMessage(error, "加载失败，请重试")
          }
        />
      ) : (
        <div className="mt-6 space-y-4">
          {providers.map((provider) =>
            form?.mode === "edit" && form.provider.id === provider.id ? (
              <ModelKeyForm
                key={provider.id}
                providerId={provider.id}
                initialLabel={provider.label}
                initialBaseUrl={provider.base_url}
                initialModel={provider.default_model}
                hideTestHint
                onSaved={onSavedProvider}
                onCancel={() => setForm(null)}
              />
            ) : (
              <ProviderCard
                key={provider.id}
                provider={provider}
                testing={testingId === provider.id}
                testMessage={testMessage[provider.id]}
                actionError={cardError[provider.id]}
                onTest={() => void runTest(provider.id)}
                onEdit={() => setForm({ mode: "edit", provider })}
                onDelete={() => setPendingDelete(provider)}
              />
            ),
          )}

          {providers.length === 0 && form?.mode !== "add" && (
            <EmptyProviders onAdd={() => setForm({ mode: "add" })} />
          )}

          {form?.mode === "add" ? (
            <ModelKeyForm
              hideTestHint
              onSaved={onSavedProvider}
              onCancel={() => setForm(null)}
            />
          ) : form === null && providers.length > 0 ? (
            <Button
              variant="neutral"
              size="md"
              icon={<Plus size={14} />}
              onClick={() => setForm({ mode: "add" })}
            >
              添加服务商
            </Button>
          ) : null}

          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              测连绿≠可聊天；自定义 Base URL 常需 /v1
            </p>
            <InfoNote />
          </div>
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(next) => {
          if (!next) setPendingDelete(null);
        }}
        tone="danger"
        title={
          pendingDelete
            ? `删除服务商「${providerName(pendingDelete)}」？`
            : "删除服务商？"
        }
        description={deleteConsequence()}
        confirmLabel="删除"
        busy={deleting}
        onConfirm={() => {
          if (pendingDelete) void removeProvider(pendingDelete);
        }}
      />
    </div>
  );
}

function providerName(provider: LlmProviderView): string {
  return provider.label?.trim() || hostFromBaseUrl(provider.base_url);
}

function hostFromBaseUrl(url: string | null | undefined): string {
  const trimmed = url?.trim();
  if (!trimmed) return "";
  try {
    return new URL(trimmed).host;
  } catch {
    return trimmed;
  }
}

function StatusBadge({
  status,
  message,
  testing,
}: {
  status: string;
  message?: string | null;
  testing?: boolean;
}) {
  if (testing) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 size={14} className="animate-spin" />
        测试中…
      </span>
    );
  }
  if (status === "active") {
    return (
      <span className="flex items-center gap-1.5 text-xs text-success">
        <CheckCircle2 size={14} />
        {message ?? "连接正常"}
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <XCircle size={14} />
        {message ?? "连接失败"}
      </span>
    );
  }
  return <span className="text-xs text-muted-foreground">未测试</span>;
}

function ProviderCard({
  provider,
  testing,
  testMessage,
  actionError,
  onTest,
  onEdit,
  onDelete,
}: {
  provider: LlmProviderView;
  testing: boolean;
  testMessage?: string | null;
  actionError?: string | null;
  onTest: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const host = hostFromBaseUrl(provider.base_url);
  const busy = testing;
  const testModel = provider.default_model?.trim();
  const metaParts = [
    host || null,
    provider.masked_key ?? "已配置",
    testModel ? `测试用模型 ${testModel}` : null,
  ].filter(Boolean);

  return (
    <Card className="px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-medium text-foreground">
              {providerName(provider)}
            </p>
            <StatusBadge
              status={provider.status}
              message={testMessage}
              testing={testing}
            />
            <ToolsCapabilityBadge supportsTools={provider.supports_tools} />
          </div>
          <p className="truncate font-mono text-xs text-muted-foreground">
            {metaParts.join(" · ")}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="neutral"
            size="sm"
            disabled={busy}
            icon={
              testing ? (
                <Loader2 size={14} className="animate-spin" />
              ) : undefined
            }
            onClick={onTest}
          >
            测试
          </Button>
          <Button variant="neutral" size="sm" disabled={busy} onClick={onEdit}>
            编辑
          </Button>
          <Button variant="danger" size="sm" disabled={busy} onClick={onDelete}>
            删除
          </Button>
        </div>
      </div>
      {actionError && (
        <p className="mt-2 text-xs text-muted-foreground">{actionError}</p>
      )}
    </Card>
  );
}

function EmptyProviders({ onAdd }: { onAdd: () => void }) {
  return (
    <SettingsAsync
      variant="card"
      empty
      emptyLabel="还没有接入服务商。"
      emptyAction={
        <Button size="md" icon={<Plus size={14} />} onClick={onAdd}>
          添加服务商
        </Button>
      }
    />
  );
}

function InfoNote() {
  return (
    <p className="flex items-start gap-2 text-xs text-muted-foreground">
      <ShieldCheck
        size={14}
        className="mt-0.5 shrink-0 text-muted-foreground"
      />
      <span>
        Key 经 AES-256-GCM 加密存储，服务端只显示后 4 位。对话使用「设置 ·
        模型」里的组合；平台只统计 token，不代为计价。
      </span>
    </p>
  );
}
