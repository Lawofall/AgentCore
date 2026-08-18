import {
  type LlmProviderView,
  type LlmProvidersResponse,
  deleteLlmProvider,
  listLlmProviders,
  testLlmProvider,
} from "@/api/llmProviders";
import {
  type CreateLlmModelProfileRequest,
  type LlmModelProfileView,
  type ModelProfileSlot,
  createModelProfile,
  deleteModelProfile,
  invalidateModelProfilesCache,
  listModelProfiles,
  profileSlotsSummary,
  setDefaultModelProfile,
  updateModelProfile,
} from "@/api/modelProfiles";
import {
  type ModelCatalog,
  type ModelCatalogItem,
  findCatalogItem,
  unavailableReasonCopy,
  useModels,
} from "@/api/models";
import { ConfirmDialog } from "@/components/conversations";
import { ProviderForm } from "@/pages/more/ProviderForm";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import "@/pages/more/more.css";

// 设置·模型配置 — BYOK providers + 模型组合管理.
//
// Chat picks combinations only; this page creates/edits combinations (main required;
// worker/background empty = follow main; vision empty = 不配置, does not follow main)

/** 草稿主模型是否在 curated 目录标有 vision（贴图可直送主模型）。 */
function mainHasCuratedVision(
  main: ModelProfileSlot | null,
  catalog: ModelCatalog | null,
): boolean {
  if (!main) return false;
  const item = findCatalogItem(catalog, {
    id: main.model,
    origin: main.origin,
    providerId: main.provider_id,
  });
  return (item?.capabilities ?? []).includes("vision");
}
// and sets the account default.

function endpointHost(baseUrl: string): string {
  const trimmed = baseUrl.trim();
  if (!trimmed) return "";
  try {
    return new URL(trimmed).host;
  } catch {
    return trimmed.replace(/^https?:\/\//, "").split("/")[0] ?? trimmed;
  }
}

function capabilityLabel(supportsTools: boolean | null | undefined): string {
  if (supportsTools === true) return "支持工具调用";
  if (supportsTools === false) return "仅对话";
  return "未测试能力";
}

/** A titled option group for slot selectors (platform or BYOK). */
type SlotModelItem = {
  id: string;
  display_name: string;
  value: string;
  available?: boolean;
  unavailable_reason?: ModelCatalogItem["unavailable_reason"];
};

type ProviderModelGroup = {
  key: string;
  title: string;
  items: SlotModelItem[];
};

const PLATFORM_POINTER_ID = "__platform__";

function encodeSlot(slot: ModelProfileSlot): string {
  if (slot.origin === "platform" || !slot.provider_id) {
    return `${PLATFORM_POINTER_ID}::${slot.model}`;
  }
  return `${slot.provider_id}::${slot.model}`;
}

function decodeSlot(value: string): ModelProfileSlot | null {
  const i = value.indexOf("::");
  if (i < 0) return null;
  const provider_id = value.slice(0, i);
  const model = value.slice(i + 2);
  if (!provider_id || !model) return null;
  if (provider_id === PLATFORM_POINTER_ID) {
    return { origin: "platform", provider_id: null, model };
  }
  return { origin: "byok", provider_id, model };
}

/**
 * Per-provider option groups for slot selectors.
 * BYOK candidates = catalog rows ∪ provider.default_model ∪ live slot models;
 * platform group from catalog (+ optional platform_model fallback); unavailable
 * rows stay listed so the picker can grey them and show why.
 * 有 BYOK 时槽位用 combobox 手填；仅 platform 时用本分组喂纯 select。
 */
function defaultModelGroups(
  catalog: ModelCatalog | null,
  providers: LlmProviderView[],
  platformModel?: string | null,
  ...slots: (ModelProfileSlot | null | undefined)[]
): ProviderModelGroup[] {
  const groups: ProviderModelGroup[] = [];

  const platformItems: ProviderModelGroup["items"] = [];
  const platformSeen = new Set<string>();
  const addPlatform = (
    id: string,
    displayName: string,
    extra?: Pick<SlotModelItem, "available" | "unavailable_reason">,
  ) => {
    const m = id.trim();
    if (!m || platformSeen.has(m)) return;
    platformSeen.add(m);
    platformItems.push({
      id: m,
      display_name: displayName.trim() || m,
      value: `${PLATFORM_POINTER_ID}::${m}`,
      available: extra?.available !== false,
      unavailable_reason: extra?.unavailable_reason,
    });
  };
  for (const item of catalog?.models ?? []) {
    if (item.origin !== "platform") continue;
    addPlatform(item.id, item.display_name, {
      available: item.available,
      unavailable_reason: item.unavailable_reason,
    });
  }
  const fallback = platformModel?.trim();
  if (fallback) addPlatform(fallback, fallback);
  if (platformItems.length > 0) {
    groups.push({
      key: PLATFORM_POINTER_ID,
      title: "平台额度",
      items: platformItems,
    });
  }

  for (const p of providers) {
    const items: ProviderModelGroup["items"] = [];
    const seen = new Set<string>();
    const add = (
      id: string,
      displayName?: string | null,
      extra?: Pick<SlotModelItem, "available" | "unavailable_reason">,
    ) => {
      const m = id.trim();
      if (!m || seen.has(m)) return;
      seen.add(m);
      items.push({
        id: m,
        display_name: displayName?.trim() || m,
        value: `${p.id}::${m}`,
        available: extra?.available !== false,
        unavailable_reason: extra?.unavailable_reason,
      });
    };
    for (const item of catalog?.models ?? []) {
      if (item.origin === "byok" && item.provider_id === p.id) {
        add(item.id, item.display_name, {
          available: item.available,
          unavailable_reason: item.unavailable_reason,
        });
      }
    }
    if (p.default_model) add(p.default_model);
    groups.push({
      key: p.id,
      title: p.label?.trim() || endpointHost(p.base_url) || p.id,
      items,
    });
  }

  for (const slot of slots) {
    if (!slot?.model) continue;
    const groupKey =
      slot.origin === "platform" || !slot.provider_id
        ? PLATFORM_POINTER_ID
        : slot.provider_id;
    let group = groups.find((g) => g.key === groupKey);
    if (!group && groupKey === PLATFORM_POINTER_ID) {
      group = { key: PLATFORM_POINTER_ID, title: "平台额度", items: [] };
      groups.unshift(group);
    }
    if (group && !group.items.some((i) => i.id === slot.model)) {
      group.items.unshift({
        id: slot.model,
        display_name: slot.model,
        value: encodeSlot(slot),
      });
    }
  }
  return groups;
}

/** groups 内有可选（非置灰）项才算有目录可选。 */
function hasSelectableModels(groups: ProviderModelGroup[]): boolean {
  return groups.some((g) => g.items.some((i) => i.available !== false));
}

/** 存在 BYOK 服务商分组时，即使目录为空也可手填 model id。 */
function hasByokProviderGroups(groups: ProviderModelGroup[]): boolean {
  return groups.some((g) => g.key !== PLATFORM_POINTER_ID);
}

/** 目录项或 BYOK 自定义均可选。 */
function canChooseModel(groups: ProviderModelGroup[]): boolean {
  return hasSelectableModels(groups) || hasByokProviderGroups(groups);
}

type AdvancedSlotDraft = {
  worker: ModelProfileSlot | null;
  background: ModelProfileSlot | null;
  vision: ModelProfileSlot | null;
};

function hasAdvancedSlotOverrides(draft: AdvancedSlotDraft): boolean {
  return Boolean(draft.worker || draft.background || draft.vision);
}

/** 高级区收起时的一行摘要。 */
function advancedSlotsSummary(
  worker: ModelProfileSlot | null,
  background: ModelProfileSlot | null,
  vision: ModelProfileSlot | null,
): string {
  if (!worker && !background && !vision) {
    return "Worker/后台：跟随主模型 · 识图：不配置";
  }
  const workerLabel = worker?.model ?? "跟随主模型";
  const backgroundLabel = background?.model ?? "跟随主模型";
  const visionLabel = vision?.model ?? "不配置";
  return `Worker：${workerLabel} · 后台：${backgroundLabel} · 识图：${visionLabel}`;
}

/** 识图槽：优先只列 catalog 带 `vision` capability 的项；过滤为空则回退全目录。 */
function catalogForVisionSlot(
  catalog: ModelCatalog | null,
): ModelCatalog | null {
  if (!catalog) return catalog;
  const visionModels = catalog.models.filter((m) =>
    (m.capabilities ?? []).includes("vision"),
  );
  if (visionModels.length === 0) return catalog;
  return { ...catalog, models: visionModels };
}

/**
 * 识图下拉分组。过滤命中时去掉 provider.default_model，避免无 vision 的默认项渗入；
 * 过滤为空时与主槽同形（全目录 + BYOK 手填）。
 */
function visionModelGroups(
  catalog: ModelCatalog | null,
  providers: LlmProviderView[],
  platformModel?: string | null,
  ...slots: (ModelProfileSlot | null | undefined)[]
): ProviderModelGroup[] {
  const visionCatalog = catalogForVisionSlot(catalog);
  const filtered = visionCatalog !== catalog;
  const providersForVision = filtered
    ? providers.map((p) => ({ ...p, default_model: "" }))
    : providers;
  return defaultModelGroups(
    visionCatalog,
    providersForVision,
    platformModel,
    ...slots,
  );
}

type Surface =
  | { kind: "list" }
  | { kind: "provider"; mode: "add" }
  | { kind: "provider"; mode: "edit"; provider: LlmProviderView }
  | { kind: "profile"; mode: "new" }
  | { kind: "profile"; mode: "edit"; profile: LlmModelProfileView };

export function ModelSettings() {
  const navigate = useNavigate();
  const [data, setData] = useState<LlmProvidersResponse | null>(null);
  const [profiles, setProfiles] = useState<LlmModelProfileView[]>([]);
  const [defaultProfileId, setDefaultProfileId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [surface, setSurface] = useState<Surface>({ kind: "list" });
  const [deleteTarget, setDeleteTarget] = useState<LlmProviderView | null>(
    null,
  );
  const [deleteProfileTarget, setDeleteProfileTarget] =
    useState<LlmModelProfileView | null>(null);
  const [deleting, setDeleting] = useState(false);
  const { data: catalog, refetch: refetchCatalog } = useModels();

  async function loadProfiles() {
    const res = await listModelProfiles();
    setProfiles(res.data);
    setDefaultProfileId(res.default_model_profile_id ?? null);
    invalidateModelProfilesCache();
  }

  function loadInitial() {
    setLoading(true);
    setLoadError(null);
    Promise.all([listLlmProviders(), listModelProfiles()])
      .then(([providers, profileList]) => {
        setData(providers);
        setProfiles(profileList.data);
        setDefaultProfileId(profileList.default_model_profile_id ?? null);
        invalidateModelProfilesCache();
      })
      .catch((e: unknown) =>
        setLoadError(
          e instanceof Error && e.message ? e.message : "加载失败，请重试",
        ),
      )
      .finally(() => setLoading(false));
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: loadInitial is stable; run once on mount
  useEffect(() => {
    loadInitial();
  }, []);

  async function reload() {
    const next = await listLlmProviders();
    setData(next);
    await loadProfiles();
    refetchCatalog();
  }

  const platformMode = data?.platform_available === true;

  async function confirmDeleteProvider() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteLlmProvider(deleteTarget.id);
      setDeleteTarget(null);
      await reload();
    } catch {
      /* keep dialog open */
    } finally {
      setDeleting(false);
    }
  }

  async function confirmDeleteProfile() {
    if (!deleteProfileTarget) return;
    setDeleting(true);
    try {
      await deleteModelProfile(deleteProfileTarget.id);
      setDeleteProfileTarget(null);
      await loadProfiles();
    } catch {
      /* keep dialog open */
    } finally {
      setDeleting(false);
    }
  }

  const onList = surface.kind === "list";
  const title =
    surface.kind === "provider"
      ? "模型配置"
      : surface.kind === "profile"
        ? surface.mode === "new"
          ? "新建组合"
          : "编辑组合"
        : "模型配置";

  return (
    <div className="screen">
      <header className="bar">
        <button
          type="button"
          className="link"
          onClick={() =>
            onList ? navigate("/more") : setSurface({ kind: "list" })
          }
        >
          ← {onList ? "设置" : "模型配置"}
        </button>
        <span>{title}</span>
        <span style={{ width: 44 }} />
      </header>

      <div className="settings-body">
        {surface.kind === "provider" ? (
          <ProviderForm
            provider={surface.mode === "edit" ? surface.provider : undefined}
            onSaved={() => {
              setSurface({ kind: "list" });
              void reload();
            }}
            onCancel={() => setSurface({ kind: "list" })}
          />
        ) : surface.kind === "profile" ? (
          <ProfileForm
            profile={surface.mode === "edit" ? surface.profile : undefined}
            catalog={catalog}
            providers={data?.providers ?? []}
            platformModel={data?.platform_model}
            platformAvailable={platformMode}
            onSaved={() => {
              setSurface({ kind: "list" });
              void loadProfiles();
            }}
            onCancel={() => setSurface({ kind: "list" })}
          />
        ) : loading ? (
          <p className="muted hint">加载中…</p>
        ) : loadError ? (
          <div className="hint">
            <p className="error">{loadError}</p>
            <button
              type="button"
              onClick={loadInitial}
              style={{ marginTop: 12 }}
            >
              重试
            </button>
          </div>
        ) : data ? (
          <>
            <p className="settings-desc">
              {platformMode
                ? "接入你自己的 OpenAI 兼容服务商为高级选项——不接入也可用平台额度直接对话。可添加多个服务商，按你的端点自担费用。Key 经 AES 加密存储，仅回显后 4 位。"
                : "需自行在 jiurelay 免费配额度或接入服务商后才能对话。可添加多个 OpenAI 兼容服务商（API Key、Base URL）。日常选用请到「模型组合」。Key 经 AES 加密存储，仅回显后 4 位。"}
            </p>

            <ProfilesSection
              profiles={profiles}
              defaultProfileId={defaultProfileId}
              catalog={catalog}
              onNew={() => setSurface({ kind: "profile", mode: "new" })}
              onEdit={(profile) =>
                setSurface({ kind: "profile", mode: "edit", profile })
              }
              onDelete={(profile) => setDeleteProfileTarget(profile)}
              onSetDefault={async (id) => {
                await setDefaultModelProfile(id);
                await loadProfiles();
              }}
            />

            <h2 className="section-title" style={{ marginTop: 20 }}>
              服务商
            </h2>
            <p className="section-note">
              测连绿≠可聊天；自定义 Base URL 通常需含 /v1。
            </p>
            <ProviderList
              providers={data.providers}
              onTest={async (id) => {
                const updated = await testLlmProvider(id);
                setData((prev) =>
                  prev
                    ? {
                        ...prev,
                        providers: prev.providers.map((p) =>
                          p.id === updated.id ? updated : p,
                        ),
                      }
                    : prev,
                );
                refetchCatalog();
              }}
              onEdit={(provider) =>
                setSurface({ kind: "provider", mode: "edit", provider })
              }
              onDelete={(provider) => setDeleteTarget(provider)}
            />

            <button
              type="button"
              className="btn-outline add-provider-btn"
              onClick={() => setSurface({ kind: "provider", mode: "add" })}
            >
              ＋ 添加服务商
            </button>

            <InfoNote />
          </>
        ) : null}
      </div>

      {deleteTarget && (
        <ConfirmDialog
          title="删除服务商"
          message={`删除「${deleteTarget.label || endpointHost(deleteTarget.base_url) || "该服务商"}」后，指向它的组合槽位需重新选择。此操作不可撤销。`}
          confirmLabel={deleting ? "删除中…" : "删除"}
          busy={deleting}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => void confirmDeleteProvider()}
        />
      )}

      {deleteProfileTarget && (
        <ConfirmDialog
          title="删除组合"
          message={`删除「${deleteProfileTarget.name}」后，使用该组合的会话将回落到账号默认。此操作不可撤销。`}
          confirmLabel={deleting ? "删除中…" : "删除"}
          busy={deleting}
          onCancel={() => setDeleteProfileTarget(null)}
          onConfirm={() => void confirmDeleteProfile()}
        />
      )}
    </div>
  );
}

function ProfilesSection({
  profiles,
  defaultProfileId,
  catalog,
  onNew,
  onEdit,
  onDelete,
  onSetDefault,
}: {
  profiles: LlmModelProfileView[];
  defaultProfileId: string | null;
  catalog: ModelCatalog | null;
  onNew: () => void;
  onEdit: (p: LlmModelProfileView) => void;
  onDelete: (p: LlmModelProfileView) => void;
  onSetDefault: (id: string) => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Prefer user + system; still show selected implicits if any remain.
  const visible = profiles.filter((p) => p.kind !== "implicit");

  async function makeDefault(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await onSetDefault(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "设置默认失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="section" data-testid="profiles-section">
      <h2 className="section-title">模型组合</h2>
      <p className="section-note">
        主模型必填；Worker / 后台可留空跟随；识图可留空不配置（高级 ·
        分槽覆盖）。改定义后下一回合生效。
      </p>
      <p className="section-note">
        多人协作（委派）对工具调用要求较高；若失败可换更稳的主模型，或改用手写{" "}
        <code>tasks</code>。
      </p>

      {visible.length === 0 ? (
        <p className="muted hint" data-testid="profiles-empty">
          还没有组合。
        </p>
      ) : (
        <div className="provider-list">
          {visible.map((p) => {
            const isDefault = p.is_default || p.id === defaultProfileId;
            const canDelete = p.kind === "user";
            const canEdit = p.kind === "user";
            return (
              <div
                key={p.id}
                className="section-card provider-card"
                data-testid={`profile-card-${p.id}`}
              >
                <div className="provider-head">
                  <span className="provider-label">{p.name}</span>
                  <span className="provider-default-badges">
                    {isDefault && (
                      <span className="provider-badge">账号默认</span>
                    )}
                    {p.kind === "system" && (
                      <span className="provider-badge">预置</span>
                    )}
                  </span>
                </div>
                <p className="provider-model muted">
                  {profileSlotsSummary(catalog, p)}
                </p>
                <div className="btn-row">
                  {canEdit && (
                    <button
                      type="button"
                      className="btn-outline"
                      onClick={() => onEdit(p)}
                      disabled={busyId !== null}
                    >
                      编辑
                    </button>
                  )}
                  {!isDefault && (
                    <button
                      type="button"
                      className="btn-outline"
                      onClick={() => void makeDefault(p.id)}
                      disabled={busyId !== null}
                    >
                      {busyId === p.id ? "设置中…" : "设为默认"}
                    </button>
                  )}
                  {canDelete && (
                    <button
                      type="button"
                      className="btn-danger-outline"
                      onClick={() => onDelete(p)}
                      disabled={busyId !== null}
                    >
                      删除
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <button
        type="button"
        className="btn-outline add-provider-btn"
        data-testid="profile-new"
        onClick={onNew}
      >
        ＋ 新建组合
      </button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function slotProviderKey(slot: ModelProfileSlot | null): string {
  if (!slot) return "";
  if (slot.origin === "platform" || !slot.provider_id)
    return PLATFORM_POINTER_ID;
  return slot.provider_id;
}

type SlotModelSelectProps = {
  id: string;
  label: string;
  groups: ProviderModelGroup[];
  providers: LlmProviderView[];
  value: string;
  followLabel?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
};

/**
 * Slot picker.
 * - 有 BYOK 分组：始终「服务商下拉 + 模型 id 输入」；目录项进 datalist 建议，可直接粘贴任意 id。
 * - 仅 platform：纯 select，无手填。
 * `value` 为编码 pointer，或 ""（可选槽 = follow / 不配置）。
 */
function SlotModelSelect(props: SlotModelSelectProps) {
  if (hasByokProviderGroups(props.groups)) {
    return <SlotModelCombobox {...props} />;
  }
  return <SlotModelPlatformSelect {...props} />;
}

/** 仅 platform、无 BYOK：纯目录 select。 */
function SlotModelPlatformSelect({
  id,
  label,
  groups,
  value,
  followLabel,
  disabled,
  onChange,
}: SlotModelSelectProps) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        className="text-input"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        data-testid={`${id}-select`}
      >
        {followLabel !== undefined ? (
          <option value="">{followLabel}</option>
        ) : (
          !value && (
            <option value="" disabled>
              选择模型
            </option>
          )
        )}
        {groups.map((g) => (
          <optgroup key={g.key} label={g.title}>
            {g.items.map((m) => {
              const unavailable = m.available === false;
              const reason = unavailable
                ? unavailableReasonCopy(m.unavailable_reason)
                : null;
              return (
                <option key={m.value} value={m.value} disabled={unavailable}>
                  {unavailable && reason
                    ? `${m.display_name}（${reason}）`
                    : m.display_name}
                </option>
              );
            })}
          </optgroup>
        ))}
      </select>
    </div>
  );
}

/** 有 BYOK：服务商 + 可手填 model id（datalist 建议）。 */
function SlotModelCombobox({
  id,
  label,
  groups,
  providers,
  value,
  followLabel,
  disabled,
  onChange,
}: SlotModelSelectProps) {
  const decoded = value ? decodeSlot(value) : null;
  const platformGroup = groups.find((g) => g.key === PLATFORM_POINTER_ID);

  const providerOptions: { id: string; title: string }[] = [];
  if (platformGroup) {
    providerOptions.push({
      id: PLATFORM_POINTER_ID,
      title: platformGroup.title,
    });
  }
  for (const p of providers) {
    providerOptions.push({
      id: p.id,
      title: p.label?.trim() || endpointHost(p.base_url) || p.id,
    });
  }
  // 孤儿 BYOK provider_id 不在列表时仍回显可选。
  if (
    decoded?.origin === "byok" &&
    decoded.provider_id &&
    !providerOptions.some((o) => o.id === decoded.provider_id)
  ) {
    providerOptions.push({
      id: decoded.provider_id,
      title: decoded.provider_id,
    });
  }

  const [providerId, setProviderId] = useState(
    () =>
      slotProviderKey(decoded) ||
      providerOptions[0]?.id ||
      providers[0]?.id ||
      "",
  );
  const [model, setModel] = useState(() => (decoded ? decoded.model : ""));

  useEffect(() => {
    const next = value ? decodeSlot(value) : null;
    if (next) {
      const key = slotProviderKey(next);
      if (key) setProviderId(key);
      // 已有手填内容时勿用 decode 回写，避免 trim 后吞掉正在输入的空格。
      setModel((prev) => (prev.trim() === next.model ? prev : next.model));
      return;
    }
    setModel("");
  }, [value]);

  const suggestions = groups.find((g) => g.key === providerId)?.items ?? [];
  const datalistId = `${id}-suggestions`;

  function emit(nextProviderId: string, nextModel: string) {
    const m = nextModel.trim();
    if (nextProviderId && m) onChange(`${nextProviderId}::${m}`);
    else onChange("");
  }

  return (
    <div className="field" data-testid={`${id}-combobox`}>
      <span className="field-label">{label}</span>
      <label className="field-label" htmlFor={`${id}-provider`}>
        服务商
      </label>
      <select
        id={`${id}-provider`}
        className="text-input"
        value={providerId}
        disabled={disabled}
        data-testid={`${id}-provider`}
        onChange={(e) => {
          const pid = e.target.value;
          setProviderId(pid);
          const nextSuggestions =
            groups.find((g) => g.key === pid)?.items ?? [];
          const trimmed = model.trim();
          // 新渠道仍有该 id → 保留；否则清掉，避免渠道/模型错配静默保存。
          if (trimmed && nextSuggestions.some((s) => s.id === trimmed)) {
            emit(pid, model);
            return;
          }
          if (followLabel !== undefined) {
            setModel("");
            onChange("");
            return;
          }
          const provider = providers.find((p) => p.id === pid);
          const defaultId = provider?.default_model?.trim() ?? "";
          const selectable = nextSuggestions.filter(
            (s) => s.available !== false,
          );
          const fallback =
            (defaultId && selectable.some((s) => s.id === defaultId)
              ? defaultId
              : "") ||
            selectable[0]?.id ||
            "";
          setModel(fallback);
          emit(pid, fallback);
        }}
      >
        {providerOptions.map((o) => (
          <option key={o.id} value={o.id}>
            {o.title}
          </option>
        ))}
      </select>
      <label
        className="field-label"
        htmlFor={`${id}-model`}
        style={{ marginTop: 8 }}
      >
        模型 ID
      </label>
      <input
        id={`${id}-model`}
        className="text-input"
        value={model}
        disabled={disabled}
        list={datalistId}
        placeholder={
          followLabel !== undefined
            ? `${followLabel}，或填写模型 ID`
            : "model id，如 ep-xxxx"
        }
        data-testid={`${id}-model`}
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => {
          const m = e.target.value;
          setModel(m);
          emit(providerId, m);
        }}
      />
      <datalist id={datalistId}>
        {suggestions.map((s) => {
          const reason =
            s.available === false
              ? unavailableReasonCopy(s.unavailable_reason)
              : null;
          return (
            <option
              key={s.id}
              value={s.id}
              label={reason ? `${s.display_name}（${reason}）` : s.display_name}
            />
          );
        })}
      </datalist>
      {suggestions
        .filter((s) => s.available === false)
        .map((s) => {
          const reason = unavailableReasonCopy(s.unavailable_reason);
          if (!reason) return null;
          return (
            <p
              key={s.id}
              className="muted"
              style={{ fontSize: 12, marginTop: 4 }}
              data-testid={`${id}-unavailable-${s.id}`}
            >
              {s.display_name}不可选：{reason}
            </p>
          );
        })}
      {followLabel !== undefined ? (
        value ? (
          <button
            type="button"
            className="btn-outline"
            style={{ marginTop: 8 }}
            disabled={disabled}
            data-testid={`${id}-clear`}
            onClick={() => {
              setModel("");
              onChange("");
            }}
          >
            {followLabel}
          </button>
        ) : (
          <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            {followLabel}
          </p>
        )
      ) : (
        <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          可从建议选择或直接粘贴（火山 ep-、中转私有 id 等）。
        </p>
      )}
    </div>
  );
}

function ProfileForm({
  profile,
  catalog,
  providers,
  platformModel,
  platformAvailable,
  onSaved,
  onCancel,
}: {
  profile?: LlmModelProfileView;
  catalog: ModelCatalog | null;
  providers: LlmProviderView[];
  platformModel?: string | null;
  platformAvailable: boolean;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const editing = Boolean(profile);
  const groups = defaultModelGroups(
    catalog,
    providers,
    platformModel,
    profile?.main,
    profile?.worker,
    profile?.background,
  );
  const visionGroups = visionModelGroups(
    catalog,
    providers,
    platformModel,
    profile?.vision,
  );
  const firstMain =
    groups
      .find((g) => g.items.some((i) => i.available !== false))
      ?.items.find((i) => i.available !== false)?.value ??
    (platformModel ? `${PLATFORM_POINTER_ID}::${platformModel}` : "");
  const canChoose = canChooseModel(groups);
  const canChooseVision = canChooseModel(visionGroups);
  const showEmptyGuide = !canChoose;

  const [name, setName] = useState(profile?.name ?? "");
  const [mainValue, setMainValue] = useState(
    profile ? encodeSlot(profile.main) : firstMain,
  );
  const [workerValue, setWorkerValue] = useState(
    profile?.worker ? encodeSlot(profile.worker) : "",
  );
  const [backgroundValue, setBackgroundValue] = useState(
    profile?.background ? encodeSlot(profile.background) : "",
  );
  const [visionValue, setVisionValue] = useState(
    profile?.vision ? encodeSlot(profile.vision) : "",
  );
  const [advancedOpen, setAdvancedOpen] = useState(() =>
    hasAdvancedSlotOverrides({
      worker: profile?.worker ?? null,
      background: profile?.background ?? null,
      vision: profile?.vision ?? null,
    }),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mainDecoded = mainValue ? decodeSlot(mainValue) : null;
  const mainVisionCapable = mainHasCuratedVision(mainDecoded, catalog);
  const workerDecoded = workerValue ? decodeSlot(workerValue) : null;
  const backgroundDecoded = backgroundValue
    ? decodeSlot(backgroundValue)
    : null;
  const visionDecoded = visionValue ? decodeSlot(visionValue) : null;

  async function save() {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("请填写组合名称");
      return;
    }
    const main = decodeSlot(mainValue);
    if (!main) {
      setError("请选择主模型");
      return;
    }
    const worker = workerValue ? decodeSlot(workerValue) : null;
    const background = backgroundValue ? decodeSlot(backgroundValue) : null;
    const vision = visionValue ? decodeSlot(visionValue) : null;
    if (workerValue && !worker) {
      setError("Worker 模型无效");
      return;
    }
    if (backgroundValue && !background) {
      setError("后台模型无效");
      return;
    }
    if (visionValue && !vision) {
      setError("识图模型无效");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      if (editing && profile) {
        await updateModelProfile(profile.id, {
          name: trimmed,
          main,
          worker,
          background,
          vision,
        });
      } else {
        const body: CreateLlmModelProfileRequest = {
          name: trimmed,
          main,
          worker,
          background,
          vision,
          set_as_default: false,
        };
        await createModelProfile(body);
      }
      invalidateModelProfilesCache();
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  const slotDisabled = saving || !canChoose;
  const visionDisabled = saving || !canChooseVision;

  return (
    <div className="section" data-testid="profile-form">
      <div className="section-card">
        <div className="field">
          <label className="field-label" htmlFor="profile-name">
            名称
          </label>
          <input
            id="profile-name"
            className="text-input"
            value={name}
            disabled={saving}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：日常写作"
          />
        </div>
        <SlotModelSelect
          id="profile-main"
          label="主模型（必填）"
          groups={groups}
          providers={providers}
          value={mainValue}
          disabled={slotDisabled}
          onChange={setMainValue}
        />
        {showEmptyGuide && (
          <p
            className="muted"
            data-testid="profile-no-models"
            style={{ fontSize: 12, marginTop: 4 }}
          >
            {platformAvailable ? (
              <>暂无可用模型。请稍后重试，或到设置检查服务商与模型配置。</>
            ) : (
              <>
                暂无可用模型。请到{" "}
                <a
                  href="https://jiurelay.com/"
                  target="_blank"
                  rel="noreferrer"
                >
                  jiurelay
                </a>{" "}
                免费配额度，或先添加服务商。
              </>
            )}
          </p>
        )}

        <div className="profile-advanced" data-testid="profile-advanced">
          <button
            type="button"
            className="profile-advanced-summary"
            aria-expanded={advancedOpen}
            disabled={saving}
            onClick={() => setAdvancedOpen((open) => !open)}
          >
            <span className="profile-advanced-title">高级 · 分槽覆盖</span>
            {!advancedOpen && (
              <span className="muted profile-advanced-hint">
                {advancedSlotsSummary(
                  workerDecoded,
                  backgroundDecoded,
                  visionDecoded,
                )}
              </span>
            )}
          </button>
          {advancedOpen && (
            <div className="profile-advanced-body">
              <SlotModelSelect
                id="profile-worker"
                label="Worker 模型"
                groups={groups}
                providers={providers}
                value={workerValue}
                followLabel="跟随主模型"
                disabled={slotDisabled}
                onChange={setWorkerValue}
              />
              <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                组队队员用；辩论用主模型。留空则跟随主模型。
              </p>
              <SlotModelSelect
                id="profile-background"
                label="后台任务模型"
                groups={groups}
                providers={providers}
                value={backgroundValue}
                followLabel="跟随主模型"
                disabled={slotDisabled}
                onChange={setBackgroundValue}
              />
              <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                标题、记忆等后台任务；留空则跟随主模型。
              </p>
              <SlotModelSelect
                id="profile-vision"
                label="识图模型（可选）"
                groups={visionGroups}
                providers={providers}
                value={visionValue}
                followLabel="不配置"
                disabled={visionDisabled}
                onChange={setVisionValue}
              />
              <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                主模型不能看图时再配；留空用平台识图或不可用。
              </p>
              {mainVisionCapable && (
                <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                  当前主模型标有视觉，贴图优先走主模型；本槽仍可供白板/按需深读。
                </p>
              )}
            </div>
          )}
        </div>

        {error && <p className="error">{error}</p>}
        <div className="field-actions">
          <button
            type="button"
            className="btn-outline"
            onClick={onCancel}
            disabled={saving}
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving || !name.trim() || !mainValue}
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ProviderList({
  providers,
  onTest,
  onEdit,
  onDelete,
}: {
  providers: LlmProviderView[];
  onTest: (id: string) => Promise<void>;
  onEdit: (provider: LlmProviderView) => void;
  onDelete: (provider: LlmProviderView) => void;
}) {
  if (providers.length === 0) {
    return (
      <p className="muted hint" data-testid="providers-empty">
        还没有配置服务商。
      </p>
    );
  }
  return (
    <div className="provider-list">
      {providers.map((p) => (
        <ProviderCard
          key={p.id}
          provider={p}
          onTest={onTest}
          onEdit={onEdit}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}

function StatusBadge({
  status,
  message,
}: {
  status: string;
  message?: string | null;
}) {
  if (status === "active") {
    return (
      <span className="status-line status-ok">● {message ?? "连接正常"}</span>
    );
  }
  if (status === "error") {
    return (
      <span className="status-line status-err">● {message ?? "连接失败"}</span>
    );
  }
  return <span className="status-line status-idle">未测试</span>;
}

function ProviderCard({
  provider,
  onTest,
  onEdit,
  onDelete,
}: {
  provider: LlmProviderView;
  onTest: (id: string) => Promise<void>;
  onEdit: (provider: LlmProviderView) => void;
  onDelete: (provider: LlmProviderView) => void;
}) {
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const host = endpointHost(provider.base_url);
  const testModel = provider.default_model?.trim() ?? "";

  async function test() {
    setTesting(true);
    setError(null);
    try {
      await onTest(provider.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "测试失败，请重试");
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="section-card provider-card" data-testid="provider-card">
      <div className="provider-head">
        <span className="provider-label">
          {provider.label || host || "服务商"}
        </span>
      </div>

      {host && <p className="provider-host muted">{host}</p>}
      {testModel ? (
        <p className="provider-model muted">测试用模型 {testModel}</p>
      ) : null}
      <span className="masked-key">{provider.masked_key ?? "已配置"}</span>

      <div>
        <StatusBadge status={provider.status} message={provider.message} />
        <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>
          {capabilityLabel(provider.supports_tools)}
        </span>
      </div>

      <div className="btn-row">
        <button
          type="button"
          className="btn-outline"
          onClick={() => void test()}
          disabled={testing}
        >
          {testing ? "测试中…" : "测试连接"}
        </button>
        <button
          type="button"
          className="btn-outline"
          onClick={() => onEdit(provider)}
          disabled={testing}
        >
          编辑
        </button>
        <button
          type="button"
          className="btn-danger-outline"
          onClick={() => onDelete(provider)}
          disabled={testing}
        >
          删除
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function InfoNote() {
  return (
    <p className="section-note" style={{ marginTop: 16 }}>
      你的 Key 仅用于你自己的对话，经 AES-256-GCM 加密存储，服务端只显示后 4
      位。平台只统计 token 用量。
    </p>
  );
}
