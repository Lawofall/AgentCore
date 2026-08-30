import { Button, Card, IconButton, Input } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  type ByokProviderId,
  DEFAULT_BYOK_PROVIDER_ID,
  getByokProviderPreset,
  isCustomByokProvider,
  listByokProviderOptions,
  normalizeByokBaseUrl,
  resolveByokProviderFromConfig,
} from "@/lib/byokProviderPresets";
import { ApiError } from "@/services/api";
import {
  type LlmProviderView,
  type UpdateLlmProviderInput,
  createLlmProvider,
  updateLlmProvider,
} from "@/services/llmProviders";
import { ExternalLink, Eye, EyeOff, Loader2 } from "lucide-react";
import { useId, useState } from "react";

/** Shared chrome for `<select>` (no L2 Select yet). */
export const MODEL_CONFIG_INPUT_CLASS =
  "h-8 w-full rounded-lg border border-input bg-background px-2 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring";

/** Same phrasing as LoginPage / lib/errors — admin sessions cannot use product APIs. */
export const ADMIN_PRODUCT_FORBIDDEN_MESSAGE =
  "此账号为管理员账号，请使用管理后台登录";

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

export type ModelKeyFormProps = {
  /** Present = 编辑该服务商（PATCH）；缺省 = 新增一个服务商（POST）。 */
  providerId?: string;
  initialLabel?: string;
  initialBaseUrl?: string;
  initialModel?: string;
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
 * 主路径 = 厂商 + 名称 + Key（自定义另有 Base URL）。
 * 「连接测试用模型」进高级（仍提交 default_model；预设用 Input+datalist 可手填/粘贴，编辑保留已存值）。
 * 预设厂商：Base URL 也进高级。自定义端点：Base URL 主路径必填。
 * 对话日常选用在「模型组合」/ picker，不在本表单。
 */
export function ModelKeyForm({
  providerId,
  initialLabel = "",
  initialBaseUrl = "",
  initialModel = "",
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
  const defaultModelId = `${formId}-default-model`;
  const defaultModelListId = `${formId}-default-model-list`;
  const [apiKey, setApiKey] = useState("");
  const [providerPreset, setProviderPreset] = useState<ByokProviderId>(() =>
    resolveByokProviderFromConfig(initialBaseUrl),
  );
  const [label, setLabel] = useState(() => {
    if (initialLabel.trim()) return initialLabel;
    const preset = resolveByokProviderFromConfig(initialBaseUrl);
    return isCustomByokProvider(preset)
      ? ""
      : getByokProviderPreset(preset).label;
  });
  const [baseUrl, setBaseUrl] = useState(() => {
    if (initialBaseUrl.trim()) return initialBaseUrl;
    return getByokProviderPreset(DEFAULT_BYOK_PROVIDER_ID).baseUrl;
  });
  const [defaultModel, setDefaultModel] = useState(() => {
    if (initialModel.trim()) return initialModel;
    return getByokProviderPreset(DEFAULT_BYOK_PROVIDER_ID).defaultModel;
  });
  const [reveal, setReveal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isCustom = isCustomByokProvider(providerPreset);
  const preset = !isCustom ? getByokProviderPreset(providerPreset) : null;
  const keyHelpUrl =
    preset?.keyHelpUrl ?? "https://platform.openai.com/api-keys";

  const baseUrlOverride =
    !isCustom &&
    preset != null &&
    baseUrl.trim().length > 0 &&
    normalizeByokBaseUrl(baseUrl) !== normalizeByokBaseUrl(preset.baseUrl) &&
    !(preset.baseUrlAliases ?? []).some(
      (alias) => normalizeByokBaseUrl(alias) === normalizeByokBaseUrl(baseUrl),
    );
  /** 非预设模型或已覆盖 Base URL → 高级默认展开（与既有 baseUrlOverride 同类）。 */
  const initialModelNotInPreset = (() => {
    const resolved = resolveByokProviderFromConfig(initialBaseUrl);
    if (isCustomByokProvider(resolved)) return false;
    const model = initialModel.trim()
      ? initialModel.trim()
      : getByokProviderPreset(DEFAULT_BYOK_PROVIDER_ID).defaultModel;
    return !getByokProviderPreset(resolved).models.includes(model);
  })();
  const [advancedOpen, setAdvancedOpen] = useState(
    () => baseUrlOverride || initialModelNotInPreset,
  );

  const selectProvider = (next: ByokProviderId) => {
    const prev = providerPreset;
    setProviderPreset(next);
    if (!isCustomByokProvider(next)) {
      const p = getByokProviderPreset(next);
      setBaseUrl(p.baseUrl);
      setLabel(p.label);
      const current = defaultModel.trim();
      const oldDefault = !isCustomByokProvider(prev)
        ? getByokProviderPreset(prev).defaultModel
        : null;
      if (!current || (oldDefault != null && current === oldDefault)) {
        setDefaultModel(p.defaultModel);
      }
      // else: 当前值 ∈ 新预设 models → 保留；否则保留为自定义（勿静默冲掉）
    }
  };

  const keyOk = isEdit || apiKey.trim().length > 0;
  const canSave =
    keyOk &&
    baseUrl.trim().length > 0 &&
    defaultModel.trim().length > 0 &&
    !saving;
  const cta = submitLabel ?? (isEdit ? "保存" : "添加");

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const trimmedKey = apiKey.trim();
      if (isEdit && providerId) {
        const body: UpdateLlmProviderInput = {
          label: label.trim(),
          base_url: baseUrl.trim() || null,
          default_model: defaultModel.trim() || null,
        };
        // 省略 api_key 保留已存密文——只有用户重新填写时才带上。
        if (trimmedKey) body.api_key = trimmedKey;
        onSaved(await updateLlmProvider(providerId, body));
      } else {
        onSaved(
          await createLlmProvider({
            label: label.trim(),
            api_key: trimmedKey,
            base_url: baseUrl.trim() || null,
            default_model: defaultModel.trim() || null,
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
        <label className="block" htmlFor={providerPresetId}>
          <span className="text-xs text-muted-foreground">厂商预设</span>
          <select
            id={providerPresetId}
            value={providerPreset}
            onChange={(e) => selectProvider(e.target.value as ByokProviderId)}
            className={`mt-1 ${MODEL_CONFIG_INPUT_CLASS} font-sans`}
          >
            {listByokProviderOptions().map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>
          {!isCustom && (
            <p className="mt-1 text-xs text-muted-foreground">
              选择后将预填名称与端点；日常选用请到「模型组合」。
            </p>
          )}
        </label>
        <label className="block" htmlFor={labelId}>
          <span className="text-xs text-muted-foreground">名称</span>
          <Input
            id={labelId}
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="如 DeepSeek、火山方舟"
            autoComplete="off"
            spellCheck={false}
            className="mt-1 w-full"
          />
        </label>
        <label className="block" htmlFor={apiKeyId}>
          <span className="text-xs text-muted-foreground">
            API Key{isEdit ? "（可选）" : ""}
          </span>
          <div className="relative mt-1">
            <Input
              id={apiKeyId}
              type={reveal ? "text" : "password"}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={isEdit ? "留空则保留已保存的 Key" : "sk-..."}
              autoComplete="off"
              spellCheck={false}
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
        </label>
        {isCustom && (
          <div>
            <label className="block" htmlFor={baseUrlId}>
              <span className="text-xs text-muted-foreground">Base URL</span>
              <Input
                id={baseUrlId}
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://your-endpoint.example/v1"
                autoComplete="off"
                spellCheck={false}
                className="mt-1 w-full font-mono"
              />
            </label>
            <p className="mt-1 text-xs text-muted-foreground">
              须为 AgentCore
              云端可访问的公网地址；公司内网域名通常不可用。自定义地址通常需含
              /v1（例 https://api.example.com/v1）。
            </p>
          </div>
        )}
        <details
          className="rounded-lg border border-border/60 bg-muted/20 p-3"
          open={advancedOpen}
          onToggle={(e) => setAdvancedOpen(e.currentTarget.open)}
        >
          <summary className="cursor-pointer text-xs font-medium text-foreground">
            高级选项
          </summary>
          <div className="mt-3 space-y-3">
            {!isCustom && (
              <div>
                <label className="block" htmlFor={baseUrlId}>
                  <span className="text-xs text-muted-foreground">
                    Base URL
                  </span>
                  <Input
                    id={baseUrlId}
                    type="text"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder={preset?.baseUrl}
                    autoComplete="off"
                    spellCheck={false}
                    className="mt-1 w-full font-mono"
                  />
                </label>
                <p className="mt-1 text-xs text-muted-foreground">
                  须为 AgentCore
                  云端可访问的公网地址；公司内网域名通常不可用。自定义地址通常需含
                  /v1（例 https://api.example.com/v1）。
                </p>
              </div>
            )}
            <div>
              <label className="block" htmlFor={defaultModelId}>
                <span className="text-xs text-muted-foreground">
                  连接测试用模型
                </span>
                <Input
                  id={defaultModelId}
                  type="text"
                  value={defaultModel}
                  onChange={(e) => setDefaultModel(e.target.value)}
                  list={!isCustom ? defaultModelListId : undefined}
                  placeholder={
                    isCustom
                      ? "model-name"
                      : (preset?.defaultModel ?? "model-name")
                  }
                  autoComplete="off"
                  spellCheck={false}
                  className="mt-1 w-full font-mono"
                />
              </label>
              {!isCustom && (
                <datalist id={defaultModelListId}>
                  {(preset?.models ?? []).map((model) => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
              )}
              <p className="mt-1 text-xs text-muted-foreground">
                可直接粘贴模型
                ID；连接测试与目录兜底用。日常选用请到「模型组合」。
              </p>
            </div>
          </div>
        </details>
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
      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
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
