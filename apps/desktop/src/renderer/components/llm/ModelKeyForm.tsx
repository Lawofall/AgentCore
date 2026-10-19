import { SettingField, SettingsFormMessage } from "@/components/settings";
import {
  Button,
  Card,
  IconButton,
  Input,
  fieldSurfaceClass,
} from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  type ByokProviderId,
  DEFAULT_BYOK_PROVIDER_ID,
  canonicalizeByokApiRoot,
  getByokProviderPreset,
  isCustomByokProvider,
  listByokProviderOptions,
  parseByokVendorId,
} from "@/lib/byokProviderPresets";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api";
import {
  type LlmProviderView,
  type UpdateLlmProviderInput,
  createLlmProvider,
  updateLlmProvider,
} from "@/services/llmProviders";
import { ExternalLink, Eye, EyeOff, Loader2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

/** Shared chrome for `<select>` (no L2 Select yet). */
export const MODEL_CONFIG_INPUT_CLASS =
  "h-8 w-full rounded-lg border border-input bg-background px-2 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring";

/** Same phrasing as LoginPage / lib/errors — admin sessions cannot use product APIs. */
export const ADMIN_PRODUCT_FORBIDDEN_MESSAGE =
  "此账号为管理员账号，请使用管理后台登录";

const BASE_URL_HINT =
  "须为云端可访问的公网地址，内网通常不可用；只填域名会补 /v1，已有路径原样使用。";

/**
 * 404/501 on model-config routes usually means the desktop build is calling a
 * retired path (前后端协议硬切) — not a transient failure. Guide to 设置·关于.
 */
export const CLIENT_VERSION_MISMATCH_MESSAGE =
  "当前客户端版本过旧，请到设置 · 关于检查更新";

export function modelConfigApiErrorMessage(
  e: unknown,
  fallback: string,
): string {
  if (e instanceof ApiError) {
    if (e.code === "ADMIN_PRODUCT_FORBIDDEN") {
      return ADMIN_PRODUCT_FORBIDDEN_MESSAGE;
    }
    // Protocol hard-cut / missing route — prefer upgrade copy over generic 加载失败.
    if (e.status === 404 || e.status === 501) {
      return CLIENT_VERSION_MISMATCH_MESSAGE;
    }
    if (e.serverMessage) return e.serverMessage;
    try {
      const body = JSON.parse(e.body) as { error?: { message?: string } };
      if (body.error?.message) return body.error.message;
    } catch {
      /* non-JSON body */
    }
  }
  return fallback;
}

/** Saved-key fingerprint chip — prefix…suffix from the server, never plaintext. */
export function MaskedKeyChip({
  value,
  className,
}: {
  value: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex max-w-full truncate rounded-full bg-muted px-2.5 py-1 font-mono text-sm text-foreground",
        className,
      )}
    >
      {value}
    </span>
  );
}

export type ModelKeyFormProps = {
  /** Present = 编辑该服务商（PATCH）；缺省 = 新增一个服务商（POST）。 */
  providerId?: string;
  initialLabel?: string;
  initialBaseUrl?: string;
  /** Saved vendor identity. Edit uses this; never inferred from Base URL. */
  initialVendorId?: string;
  /** Server fingerprint of the stored key (prefix…suffix). Edit only. */
  initialMaskedKey?: string | null;
  onSaved: (provider: LlmProviderView) => void;
  onCancel?: () => void;
  /** Override primary CTA label (defaults: 保存 / 添加). */
  submitLabel?: string;
  /** Busy label while saving. */
  savingLabel?: string;
  /** When true, hide the post-save「建议测试连接」hint. */
  hideTestHint?: boolean;
};

/**
 * BYOK 服务商表单 — 设置·服务商的「添加服务商」/「编辑服务商」共用单一真相源。
 *
 * 主路径 = 厂商 + 名称 + Key + Base URL。
 * 预设厂商预填官方端点；自定义端点 Base URL 必填。
 * 对话日常选模型在输入框，不在本表单。
 */
export function ModelKeyForm({
  providerId,
  initialLabel = "",
  initialBaseUrl = "",
  initialVendorId,
  initialMaskedKey,
  onSaved,
  onCancel,
  submitLabel,
  savingLabel = "保存中…",
  hideTestHint = false,
}: ModelKeyFormProps) {
  const isEdit = !!providerId;
  const formId = useId();
  const providerPresetId = `${formId}-provider-preset`;
  const labelId = `${formId}-label`;
  const apiKeyId = `${formId}-api-key`;
  const baseUrlId = `${formId}-base-url`;
  const [apiKey, setApiKey] = useState("");
  const [replacingKey, setReplacingKey] = useState(false);
  const apiKeyRef = useRef<HTMLInputElement>(null);
  const [providerPreset, setProviderPreset] = useState<ByokProviderId>(() => {
    if (initialVendorId) return parseByokVendorId(initialVendorId);
    if (isEdit) return "custom";
    return DEFAULT_BYOK_PROVIDER_ID;
  });
  const [label, setLabel] = useState(() => {
    if (initialLabel.trim()) return initialLabel;
    const preset = initialVendorId
      ? parseByokVendorId(initialVendorId)
      : isEdit
        ? "custom"
        : DEFAULT_BYOK_PROVIDER_ID;
    return isCustomByokProvider(preset)
      ? ""
      : getByokProviderPreset(preset).label;
  });
  const [baseUrl, setBaseUrl] = useState(() => {
    if (initialBaseUrl.trim()) return initialBaseUrl;
    return getByokProviderPreset(DEFAULT_BYOK_PROVIDER_ID).baseUrl;
  });
  const [reveal, setReveal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isCustom = isCustomByokProvider(providerPreset);
  const preset = !isCustom ? getByokProviderPreset(providerPreset) : null;
  const keyHelpUrl =
    preset?.keyHelpUrl ?? "https://platform.openai.com/api-keys";
  const savedFingerprint = isEdit ? (initialMaskedKey ?? "").trim() : "";
  const showSavedChip = Boolean(savedFingerprint) && !replacingKey;

  useEffect(() => {
    if (replacingKey) apiKeyRef.current?.focus();
  }, [replacingKey]);

  const selectProvider = (next: ByokProviderId) => {
    setProviderPreset(next);
    if (!isCustomByokProvider(next)) {
      const p = getByokProviderPreset(next);
      setBaseUrl(p.baseUrl);
      setLabel(p.label);
    }
  };

  const keyOk = isEdit || apiKey.trim().length > 0;
  const canSave = keyOk && baseUrl.trim().length > 0 && !saving;
  const cta = submitLabel ?? (isEdit ? "保存" : "添加");

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const trimmedKey = apiKey.trim();
      if (isEdit && providerId) {
        const body: UpdateLlmProviderInput = {
          label: label.trim(),
          base_url: canonicalizeByokApiRoot(baseUrl.trim()) || null,
          vendor_id: providerPreset,
        };
        // 省略 api_key 保留已存密文——只有用户重新填写时才带上。
        if (trimmedKey) body.api_key = trimmedKey;
        onSaved(await updateLlmProvider(providerId, body));
      } else {
        onSaved(
          await createLlmProvider({
            label: label.trim(),
            api_key: trimmedKey,
            vendor_id: providerPreset,
            base_url: canonicalizeByokApiRoot(baseUrl.trim()) || null,
          }),
        );
      }
    } catch (e) {
      setError(modelConfigApiErrorMessage(e, "保存失败，请重试"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="p-4">
      <p className="text-sm font-medium text-foreground">
        {isEdit ? "编辑服务商" : "添加服务商"}
      </p>
      <div className="mt-3 space-y-3">
        <SettingField
          label="厂商预设"
          htmlFor={providerPresetId}
          layout="inline"
          hint={
            !isCustom
              ? "选择后将预填名称与端点；日常选模型在输入框。"
              : undefined
          }
        >
          <select
            id={providerPresetId}
            value={providerPreset}
            onChange={(e) => selectProvider(e.target.value as ByokProviderId)}
            className={`${MODEL_CONFIG_INPUT_CLASS} font-sans`}
          >
            {listByokProviderOptions().map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>
        </SettingField>
        <SettingField label="名称" htmlFor={labelId} layout="inline">
          <Input
            id={labelId}
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="如 DeepSeek、火山方舟"
            autoComplete="off"
            spellCheck={false}
          />
        </SettingField>
        <SettingField label="API Key" htmlFor={apiKeyId}>
          <div>
            {showSavedChip ? (
              <div
                className={cn(
                  fieldSurfaceClass,
                  "flex h-8 w-full items-center gap-1 pl-2.5 pr-1 focus-within:border-primary focus-within:ring-2 focus-within:ring-ring",
                )}
                // biome-ignore lint/a11y/useSemanticElements: saved-key chip plus replace control, not a form fieldset.
                role="group"
                aria-labelledby={`${apiKeyId}-label`}
              >
                <span className="min-w-0 flex-1 truncate font-mono">
                  {savedFingerprint}
                </span>
                <button
                  type="button"
                  className="inline-flex h-6 shrink-0 items-center rounded-lg px-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none"
                  onClick={() => setReplacingKey(true)}
                >
                  更换
                </button>
              </div>
            ) : (
              <div className="relative">
                <Input
                  ref={apiKeyRef}
                  id={apiKeyId}
                  type={reveal ? "text" : "password"}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={isEdit ? "粘贴新的 Key 以替换" : "sk-..."}
                  autoComplete="off"
                  spellCheck={false}
                  aria-labelledby={`${apiKeyId}-label`}
                  className="w-full pr-9 font-mono"
                />
                <SimpleTooltip label={reveal ? "隐藏" : "显示"}>
                  <IconButton
                    onClick={() => setReveal((r) => !r)}
                    aria-label={reveal ? "隐藏" : "显示"}
                    className="absolute right-1 top-1/2 size-6 -translate-y-1/2"
                  >
                    {reveal ? <EyeOff size={14} /> : <Eye size={14} />}
                  </IconButton>
                </SimpleTooltip>
              </div>
            )}
            {isEdit && savedFingerprint && !showSavedChip ? (
              <button
                type="button"
                className="mt-1 text-xs text-muted-foreground hover:underline"
                onClick={() => {
                  setApiKey("");
                  setReveal(false);
                  setReplacingKey(false);
                }}
              >
                取消更换
              </button>
            ) : null}
          </div>
        </SettingField>
        <SettingField label="Base URL" htmlFor={baseUrlId} hint={BASE_URL_HINT}>
          <Input
            id={baseUrlId}
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={
              isCustom
                ? "https://your-endpoint.example/v1"
                : (preset?.baseUrl ?? "")
            }
            autoComplete="off"
            spellCheck={false}
            className="font-mono"
          />
        </SettingField>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
        {onCancel && (
          <Button
            variant="neutral"
            size="md"
            disabled={saving}
            onClick={onCancel}
          >
            取消
          </Button>
        )}
        <Button
          size="md"
          disabled={!canSave}
          icon={
            saving ? <Loader2 size={14} className="animate-spin" /> : undefined
          }
          onClick={() => void save()}
        >
          {saving ? savingLabel : cta}
        </Button>
      </div>
      <SettingsFormMessage className="mt-3">{error}</SettingsFormMessage>
      <a
        href={keyHelpUrl}
        target="_blank"
        rel="noreferrer"
        className="mt-3 inline-flex items-center gap-1 text-xs text-primary hover:underline"
      >
        <ExternalLink size={14} />
        {isCustom
          ? "前往厂商控制台创建 API Key"
          : `前往 ${preset?.label ?? "厂商"} 创建 API Key`}
      </a>
      {!hideTestHint && (
        <p className="mt-2 text-xs text-muted-foreground">
          保存后建议点「测试连接」确认可用，并查看是否支持工具调用。
        </p>
      )}
    </Card>
  );
}
