import { modelConfigApiErrorMessage } from "@/components/llm/ModelKeyForm";
import {
  SettingField,
  SettingsAsync,
  SettingsFormMessage,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import {
  Button,
  ConfirmDialog,
  IconButton,
  Input,
  PageHeader,
} from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import {
  type DefaultProviderGroup,
  buildDefaultProviderGroups,
  decodePointer,
  encodePointer,
  pointerValue,
} from "@/lib/llmDefaults";
import {
  llmModelProfileKeys,
  llmProviderKeys,
  modelKeys,
} from "@/lib/queryKeys";
import { cn } from "@/lib/utils";
import {
  type CreateLlmModelProfileInput,
  type LlmModelProfileView,
  type ModelProfileSlot,
  createLlmModelProfile,
  deleteLlmModelProfile,
  profileSlotSummary,
  setDefaultLlmModelProfile,
  updateLlmModelProfile,
} from "@/services/llmModelProfiles";
import type { LlmProviderView } from "@/services/llmProviders";
import { type ModelCatalogItem, slotHasCatalogVision } from "@/services/models";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronDown,
  Copy,
  Loader2,
  Pencil,
  Plus,
  Star,
  Trash2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ProfileModelSelect, canChooseFromGroups } from "./ProfileModelSelect";

/** 保存响应当次 reminders；列表/详情不带回，仅会话内按组合 id 挂住。 */
function normalizeSaveWarnings(
  warnings: string[] | null | undefined,
): string[] {
  if (!warnings?.length) return [];
  return warnings.map((w) => w.trim()).filter(Boolean);
}

/** 草稿主模型目录是否标有 vision（贴图可直送主模型）。 */
function mainHasCatalogVision(
  main: ModelProfileSlot | null,
  catalogModels: ModelCatalogItem[],
): boolean {
  if (!main) return false;
  return slotHasCatalogVision(main, catalogModels);
}

/** 从分组取第一个可选槽（平台或 BYOK），用于新建种子。 */
function firstSlotFromGroups(
  groups: DefaultProviderGroup[],
): ModelProfileSlot | null {
  for (const g of groups) {
    const m = g.models.find((opt) => opt.available !== false);
    if (!m) continue;
    return decodePointer(encodePointer(g.providerId, m.model));
  }
  return null;
}

/**
 * 无可选模型时的引导。
 * 平台可用：稍后重试 / 去设置检查（不硬推第三方）。
 * 平台不可用：接入服务商。
 */
function NoAvailableModelsGuide({
  className,
  platformAvailable,
}: {
  className?: string;
  platformAvailable: boolean;
}) {
  if (platformAvailable) {
    return (
      <p className={cn("text-xs text-muted-foreground", className)}>
        暂无可用模型。请稍后重试，或到{" "}
        <Link
          to="/more/providers"
          className="text-primary underline-offset-2 hover:underline"
        >
          设置 · 服务商
        </Link>{" "}
        检查配置。
      </p>
    );
  }
  return (
    <p className={cn("text-xs text-muted-foreground", className)}>
      暂无可用模型。请到{" "}
      <Link
        to="/more/providers"
        className="text-primary underline-offset-2 hover:underline"
      >
        接入服务商
      </Link>
      。
    </p>
  );
}

/**
 * 模型 (/more/model) — 账号默认组合 + 组合 CRUD。
 *
 * 组合 = `{ main, worker?, background?, vision? }`；账号默认组合与会话引用见
 * `/v1/users/me/llm-model-profiles`。凭据与测连见 `/more/providers`。
 */

/** 识图槽：优先只列 catalog 带 `vision` capability 的项；过滤为空则回退全目录。 */
function catalogForVisionSlot(
  catalog: ReturnType<typeof useModels>["data"],
): ReturnType<typeof useModels>["data"] {
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
function buildVisionProviderGroups(
  providers: LlmProviderView[],
  catalog: ReturnType<typeof useModels>["data"],
  ...slots: (ModelProfileSlot | null | undefined)[]
): DefaultProviderGroup[] {
  const visionCatalog = catalogForVisionSlot(catalog);
  const filtered = visionCatalog !== catalog;
  const providersForVision = filtered
    ? providers.map((p) => ({ ...p, default_model: "" }))
    : providers;
  return buildDefaultProviderGroups(
    providersForVision,
    visionCatalog,
    ...slots,
  );
}

export function ModelSettings() {
  const { data: response, isLoading, isError, error } = useLlmProviders();
  const { data: catalog } = useModels();
  const queryClient = useQueryClient();

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: llmProviderKeys.list });
    void queryClient.invalidateQueries({ queryKey: modelKeys.catalog });
    void queryClient.invalidateQueries({ queryKey: llmModelProfileKeys.list });
  };

  const providers = response?.providers ?? [];
  const platformAvailable = response?.platform_available ?? false;
  const canEditProfiles = providers.length > 0 || platformAvailable;
  const loadError =
    !isLoading && (isError || !response)
      ? modelConfigApiErrorMessage(error, "加载失败，请重试")
      : undefined;

  return (
    <div>
      <PageHeader title="模型" />

      <SettingsStack>
        <SettingsAsync loading={isLoading} error={loadError}>
          {response &&
            (canEditProfiles ? (
              <ModelProfilesSection
                providers={providers}
                catalog={catalog}
                platformAvailable={platformAvailable}
                onChanged={refresh}
              />
            ) : (
              <EmptyProfilesCta />
            ))}
        </SettingsAsync>
      </SettingsStack>
    </div>
  );
}

function EmptyProfilesCta() {
  const navigate = useNavigate();
  return (
    <SettingsAsync
      variant="card"
      empty
      emptyLabel={
        <>
          还没有可用模型。请{" "}
          <Link
            to="/more/providers"
            className="text-primary underline-offset-2 hover:underline"
          >
            接入服务商
          </Link>
          。
        </>
      }
      emptyAction={
        <Button
          size="sm"
          icon={<Plus size={14} />}
          onClick={() => navigate("/more/providers")}
        >
          接入服务商
        </Button>
      }
    />
  );
}

/**
 * 模型组合列表 + 编辑：主必填；组队队员 / 后台 / 识图收进「高级 · 其他模型」
 * （有覆盖时默认展开）。组队/后台空 = 跟随主模型；识图空 = 不配置（不 follow main）。
 * 系统预置不可删，可设默认 / 复制为用户组合；用户组合可新建 / 改名 / 删。
 */
function ModelProfilesSection({
  providers,
  catalog,
  platformAvailable,
  onChanged,
}: {
  providers: LlmProviderView[];
  catalog: ReturnType<typeof useModels>["data"];
  platformAvailable: boolean;
  onChanged: () => void;
}) {
  const {
    data: profileList,
    isLoading,
    isError,
    error,
  } = useLlmModelProfiles();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] =
    useState<LlmModelProfileView | null>(null);
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState<ReactNode>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  /** 仅来自 create/update 响应；列表 refetch 后仍靠此 map 留住可读提醒。 */
  const [saveWarningsById, setSaveWarningsById] = useState<
    Record<string, string[]>
  >({});
  const [lastSaveWarnings, setLastSaveWarnings] = useState<string[]>([]);
  const [lastWarnedProfileId, setLastWarnedProfileId] = useState<string | null>(
    null,
  );

  const rememberSaveWarnings = (
    profileId: string,
    warnings: string[] | null | undefined,
  ) => {
    const next = normalizeSaveWarnings(warnings);
    setLastSaveWarnings(next);
    setLastWarnedProfileId(next.length > 0 ? profileId : null);
    setSaveWarningsById((prev) => {
      if (next.length === 0) {
        if (!(profileId in prev)) return prev;
        const { [profileId]: _removed, ...rest } = prev;
        return rest;
      }
      return { ...prev, [profileId]: next };
    });
  };

  const catalogModels = catalog?.models ?? [];
  const manageable = useMemo(
    () =>
      (profileList?.data ?? []).filter(
        (p) => p.kind === "system" || p.kind === "user",
      ),
    [profileList],
  );

  // 仅用于新建种子 / 空目录引导；编辑卡内按当前组合槽位 fold-in。
  const seedGroups = buildDefaultProviderGroups(providers, catalog);

  const seedMain = (): ModelProfileSlot | null => {
    const cur = catalog?.current;
    if (cur?.id) {
      return {
        origin: cur.origin,
        provider_id: cur.provider_id ?? null,
        model: cur.id,
      };
    }
    const first = catalogModels.find((m) => m.available !== false);
    if (first) {
      return {
        origin: first.origin,
        provider_id: first.provider_id ?? null,
        model: first.id,
      };
    }
    return firstSlotFromGroups(seedGroups);
  };

  const withPending = async (fn: () => Promise<void>) => {
    setPending(true);
    setActionError(null);
    setSaveSuccess(null);
    setLastSaveWarnings([]);
    setLastWarnedProfileId(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setActionError(modelConfigApiErrorMessage(e, "操作失败，请重试"));
    } finally {
      setPending(false);
    }
  };

  const onSetDefault = (profile: LlmModelProfileView) =>
    withPending(async () => {
      await setDefaultLlmModelProfile(profile.id);
      setSaveSuccess(`已将「${profile.name}」设为默认组合`);
    });

  const confirmDelete = async () => {
    const profile = pendingDelete;
    if (!profile) return;
    await withPending(async () => {
      await deleteLlmModelProfile(profile.id);
      if (editingId === profile.id) setEditingId(null);
      setSaveWarningsById((prev) => {
        if (!(profile.id in prev)) return prev;
        const { [profile.id]: _removed, ...rest } = prev;
        return rest;
      });
      setSaveSuccess(`已删除「${profile.name}」`);
    });
    setPendingDelete(null);
  };

  const onCopy = (profile: LlmModelProfileView) =>
    withPending(async () => {
      const created = await createLlmModelProfile({
        name: `${profile.name} 副本`,
        main: profile.main,
        worker: profile.worker ?? null,
        background: profile.background ?? null,
        vision: profile.vision ?? null,
        set_as_default: false,
      });
      rememberSaveWarnings(created.id, created.warnings);
      setEditingId(created.id);
      setCreating(false);
      setSaveSuccess(`已复制为「${created.name}」`);
    });

  const onCreate = () => {
    const main = seedMain();
    // 无目录种子时：有 BYOK 仍可开编辑器手填；仅平台空目录 / 无服务商才拦。
    if (!main && providers.length === 0) {
      setActionError(
        <NoAvailableModelsGuide platformAvailable={platformAvailable} />,
      );
      return;
    }
    setActionError(null);
    setSaveSuccess(null);
    setLastSaveWarnings([]);
    setLastWarnedProfileId(null);
    setCreating(true);
    setEditingId(null);
  };

  const onSaveCreate = async (draft: ProfileDraft) => {
    if (!draft.main) throw new Error("主模型必填");
    setPending(true);
    try {
      const created = await createLlmModelProfile({
        name: draft.name.trim() || "未命名组合",
        main: draft.main,
        worker: draft.worker,
        background: draft.background,
        vision: draft.vision,
        set_as_default: false,
      } satisfies CreateLlmModelProfileInput);
      rememberSaveWarnings(created.id, created.warnings);
      setCreating(false);
      setEditingId(null);
      setSaveSuccess("组合已保存");
      onChanged();
    } finally {
      setPending(false);
    }
  };

  const onSaveEdit = async (
    profile: LlmModelProfileView,
    draft: ProfileDraft,
  ) => {
    if (profile.kind !== "user") return;
    if (!draft.main) throw new Error("主模型必填");
    setPending(true);
    try {
      const name = draft.name.trim() || profile.name;
      const updated = await updateLlmModelProfile(profile.id, {
        name,
        main: draft.main,
        worker: draft.worker,
        background: draft.background,
        vision: draft.vision,
      });
      rememberSaveWarnings(updated.id, updated.warnings);
      setEditingId(null);
      setSaveSuccess(`「${name}」已保存`);
      onChanged();
    } finally {
      setPending(false);
    }
  };

  return (
    <SettingsSection
      title="模型组合"
      description="主模型必填，其余槽位可留空；改动下一回合生效。"
      action={
        <Button
          variant="neutral"
          size="sm"
          icon={<Plus size={14} />}
          disabled={pending}
          onClick={onCreate}
        >
          新建
        </Button>
      }
      contentClassName="space-y-3"
    >
      <SettingsAsync
        size="sm"
        loading={isLoading}
        loadingLabel="加载组合…"
        error={
          isError ? modelConfigApiErrorMessage(error, "加载组合失败") : null
        }
        empty={manageable.length === 0 && !creating}
        emptyLabel="暂无组合"
      >
        <div className="space-y-2">
          {creating && (
            <ProfileEditor
              title="新建组合"
              providers={providers}
              catalog={catalog}
              catalogModels={catalogModels}
              platformAvailable={platformAvailable}
              initial={{
                name: "未命名组合",
                main: seedMain(),
                worker: null,
                background: null,
                vision: null,
              }}
              pending={pending}
              onCancel={() => setCreating(false)}
              onSave={onSaveCreate}
            />
          )}

          {manageable.map((profile) =>
            editingId === profile.id && profile.kind === "user" ? (
              <ProfileEditor
                key={profile.id}
                title="编辑组合"
                providers={providers}
                catalog={catalog}
                catalogModels={catalogModels}
                platformAvailable={platformAvailable}
                initial={{
                  name: profile.name,
                  main: profile.main,
                  worker: profile.worker ?? null,
                  background: profile.background ?? null,
                  vision: profile.vision ?? null,
                }}
                saveWarnings={saveWarningsById[profile.id]}
                pending={pending}
                onCancel={() => setEditingId(null)}
                onSave={(draft) => onSaveEdit(profile, draft)}
              />
            ) : (
              <ProfileListRow
                key={profile.id}
                profile={profile}
                summary={profileSlotSummary(profile, catalogModels)}
                saveWarnings={saveWarningsById[profile.id]}
                pending={pending}
                onEdit={() => {
                  setCreating(false);
                  setEditingId(profile.id);
                  setSaveSuccess(null);
                  setLastSaveWarnings([]);
                  setLastWarnedProfileId(null);
                }}
                onSetDefault={() => void onSetDefault(profile)}
                onCopy={() => void onCopy(profile)}
                onDelete={() => setPendingDelete(profile)}
              />
            ),
          )}
        </div>
      </SettingsAsync>

      <SettingsFormMessage tone="success">{saveSuccess}</SettingsFormMessage>

      {lastSaveWarnings.length > 0 &&
      !(
        lastWarnedProfileId != null &&
        manageable.some((p) => p.id === lastWarnedProfileId)
      ) ? (
        <ProfileSaveWarnings warnings={lastSaveWarnings} />
      ) : null}

      {typeof actionError === "string" ? (
        <SettingsFormMessage>{actionError}</SettingsFormMessage>
      ) : (
        actionError
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        title={`删除组合「${pendingDelete?.name ?? ""}」？`}
        description="引用该组合的会话将回落账号默认。"
        confirmLabel="删除"
        tone="danger"
        busy={pending}
        onConfirm={() => void confirmDelete()}
      />
    </SettingsSection>
  );
}

type ProfileDraft = {
  name: string;
  main: ModelProfileSlot | null;
  worker: ModelProfileSlot | null;
  background: ModelProfileSlot | null;
  vision: ModelProfileSlot | null;
};

function hasAdvancedSlotOverrides(
  draft: Pick<ProfileDraft, "worker" | "background" | "vision">,
): boolean {
  return Boolean(draft.worker || draft.background || draft.vision);
}

/** 高级区收起时的一行摘要。 */
function advancedSlotsSummary(
  worker: ModelProfileSlot | null,
  background: ModelProfileSlot | null,
  vision: ModelProfileSlot | null,
): string {
  if (!worker && !background && !vision) {
    return "组队/后台：跟随主模型 · 识图：不配置";
  }
  const workerLabel = worker?.model ?? "跟随主模型";
  const backgroundLabel = background?.model ?? "跟随主模型";
  const visionLabel = vision?.model ?? "不配置";
  return `组队：${workerLabel} · 后台：${backgroundLabel} · 识图：${visionLabel}`;
}

function ProfileSaveWarnings({
  warnings,
  className,
}: {
  warnings: string[];
  className?: string;
}) {
  if (warnings.length === 0) return null;
  return (
    <output
      className={cn(
        "block rounded-lg border border-warning/30 bg-warning/10 px-2.5 py-2 text-xs text-warning",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden />
        <div className="min-w-0 space-y-1">
          <p className="font-medium text-warning-foreground">
            已保存，但请留意模型可达性
          </p>
          <ul className="list-disc space-y-0.5 pl-4 text-warning">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      </div>
    </output>
  );
}

function ProfileListRow({
  profile,
  summary,
  saveWarnings,
  pending,
  onEdit,
  onSetDefault,
  onCopy,
  onDelete,
}: {
  profile: LlmModelProfileView;
  summary: string;
  saveWarnings?: string[];
  pending: boolean;
  onEdit: () => void;
  onSetDefault: () => void;
  onCopy: () => void;
  onDelete: () => void;
}) {
  const isUser = profile.kind === "user";
  const warnings = saveWarnings ?? [];
  const actions: {
    key: string;
    label: string;
    icon: LucideIcon;
    show: boolean;
    onClick: () => void;
  }[] = [
    {
      key: "default",
      label: "设为默认",
      icon: Star,
      show: !profile.is_default,
      onClick: onSetDefault,
    },
    { key: "copy", label: "复制", icon: Copy, show: true, onClick: onCopy },
    {
      key: "edit",
      label: "编辑",
      icon: Pencil,
      show: isUser,
      onClick: onEdit,
    },
    {
      key: "delete",
      label: "删除",
      icon: Trash2,
      show: isUser,
      onClick: onDelete,
    },
  ];
  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2",
        profile.is_default ? "border-primary/40 bg-primary/5" : "border-border",
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm text-foreground">{profile.name}</p>
            {profile.is_default && (
              <span className="rounded bg-primary/10 px-1 py-0.5 text-xs text-primary">
                默认组合
              </span>
            )}
            {profile.kind === "system" && (
              <span className="rounded bg-muted px-1 py-0.5 text-xs text-muted-foreground">
                预置
              </span>
            )}
            {warnings.length > 0 ? (
              <span className="rounded bg-warning/10 px-1 py-0.5 text-xs text-warning">
                模型提醒
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {summary}
          </p>
        </div>
        {/* 预置行没有编辑 / 删除、默认行没有设为默认：缺席的动作留一个等宽空槽，
            否则同一枚图标会在相邻行落到不同位置，右侧读起来参差不齐。 */}
        <div className="flex shrink-0 items-center gap-0.5">
          {actions.map(({ key, label, icon: Icon, show, onClick }) =>
            show ? (
              <SimpleTooltip key={key} label={label}>
                <IconButton
                  size="sm"
                  aria-label={label}
                  disabled={pending}
                  onClick={onClick}
                >
                  <Icon size={14} />
                </IconButton>
              </SimpleTooltip>
            ) : (
              <span key={key} className="size-7" aria-hidden />
            ),
          )}
        </div>
      </div>
      {warnings.length > 0 ? (
        <ProfileSaveWarnings className="mt-2" warnings={warnings} />
      ) : null}
    </div>
  );
}

/** 槽位的「清除 / 恢复跟随」——挂在 {@link SettingField} 的标签行右侧。 */
function SlotClearAction({
  label,
  disabled,
  onClear,
}: {
  label: string;
  disabled: boolean;
  onClear: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClear}
      className="text-xs text-primary underline-offset-2 hover:underline disabled:opacity-60"
    >
      {label}
    </button>
  );
}

function ProfileEditor({
  title,
  providers,
  catalog,
  catalogModels,
  platformAvailable,
  initial,
  saveWarnings,
  pending,
  onCancel,
  onSave,
}: {
  title: string;
  providers: LlmProviderView[];
  catalog: ReturnType<typeof useModels>["data"];
  catalogModels: ModelCatalogItem[];
  platformAvailable: boolean;
  initial: ProfileDraft;
  /** 上次保存响应当次提醒（如复制后立刻打开编辑器）。 */
  saveWarnings?: string[];
  pending: boolean;
  onCancel: () => void;
  onSave: (draft: ProfileDraft) => Promise<void>;
}) {
  const [name, setName] = useState(initial.name);
  const [main, setMain] = useState(initial.main);
  const [worker, setWorker] = useState(initial.worker);
  const [background, setBackground] = useState(initial.background);
  const [vision, setVision] = useState(initial.vision);
  const [advancedOpen, setAdvancedOpen] = useState(() =>
    hasAdvancedSlotOverrides(initial),
  );
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // 只 fold-in 当前编辑组合的槽位，避免跨组合污染建议。
  const groups = useMemo(
    () =>
      buildDefaultProviderGroups(
        providers,
        catalog,
        main,
        worker,
        background,
        vision,
      ),
    [providers, catalog, main, worker, background, vision],
  );
  const visionGroups = useMemo(
    () => buildVisionProviderGroups(providers, catalog, vision),
    [providers, catalog, vision],
  );

  const canChoose = canChooseFromGroups(groups);
  const canChooseVision = canChooseFromGroups(visionGroups);
  const showEmptyGuide = !canChoose;
  const mainVisionCapable = mainHasCatalogVision(main, catalogModels);
  const busy = pending || saving;

  const handleSave = async () => {
    setSaveError(null);
    setSaving(true);
    try {
      await onSave({ name, main, worker, background, vision });
    } catch (e) {
      setSaveError(modelConfigApiErrorMessage(e, "保存失败，请重试"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4 rounded-lg border border-border bg-muted/20 p-4">
      <p className="text-sm font-semibold text-foreground">{title}</p>

      <div className="max-w-md space-y-4">
        <SettingField label="名称" htmlFor="profile-name">
          <Input
            id="profile-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={busy}
          />
        </SettingField>
        <SettingField
          label="主模型"
          htmlFor="profile-main"
          hint="必填"
          hintPlacement="label"
        >
          <ProfileModelSelect
            id="profile-main"
            labelledBy="profile-main-label"
            describedBy="profile-main-hint"
            groups={groups}
            value={pointerValue(main)}
            disabled={busy}
            onChange={(value) => setMain(decodePointer(value))}
          />
          {showEmptyGuide && (
            <NoAvailableModelsGuide
              className="mt-1.5"
              platformAvailable={platformAvailable}
            />
          )}
        </SettingField>
      </div>

      <div className="border-t border-border pt-3">
        <button
          type="button"
          aria-expanded={advancedOpen}
          disabled={busy}
          onClick={() => setAdvancedOpen((open) => !open)}
          className="flex w-full max-w-md items-center gap-2 py-1 pr-2.5 text-left disabled:opacity-60"
        >
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-medium text-foreground">
              高级 · 其他模型
            </span>
            {!advancedOpen && (
              <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                {advancedSlotsSummary(worker, background, vision)}
              </span>
            )}
          </span>
          <ChevronDown
            size={14}
            className={cn(
              "shrink-0 text-muted-foreground transition-transform",
              !advancedOpen && "-rotate-90",
            )}
          />
        </button>
        {advancedOpen && (
          <div className="mt-3 max-w-md space-y-4">
            <SettingField
              label="组队队员"
              htmlFor="profile-worker"
              hint="协作时队员使用；辩论仍用主模型"
              hintPlacement="label"
              action={
                worker ? (
                  <SlotClearAction
                    label="恢复跟随"
                    disabled={busy}
                    onClear={() => setWorker(null)}
                  />
                ) : undefined
              }
            >
              <ProfileModelSelect
                id="profile-worker"
                labelledBy="profile-worker-label"
                describedBy="profile-worker-hint"
                groups={groups}
                value={pointerValue(worker)}
                disabled={busy || !canChoose}
                followLabel="跟随主模型"
                onChange={(value) => setWorker(decodePointer(value))}
              />
            </SettingField>
            <SettingField
              label="后台任务"
              htmlFor="profile-background"
              hint="标题、记忆等"
              hintPlacement="label"
              action={
                background ? (
                  <SlotClearAction
                    label="恢复跟随"
                    disabled={busy}
                    onClear={() => setBackground(null)}
                  />
                ) : undefined
              }
            >
              <ProfileModelSelect
                id="profile-background"
                labelledBy="profile-background-label"
                describedBy="profile-background-hint"
                groups={groups}
                value={pointerValue(background)}
                disabled={busy || !canChoose}
                followLabel="跟随主模型"
                onChange={(value) => setBackground(decodePointer(value))}
              />
            </SettingField>
            <SettingField
              label="识图模型（可选）"
              htmlFor="profile-vision"
              hint={
                mainVisionCapable
                  ? "主模型已可看图，本槽供白板等按需深读"
                  : "主模型不能看图时再配；否则走平台识图或不可用"
              }
              hintPlacement="label"
              action={
                vision ? (
                  <SlotClearAction
                    label="清除"
                    disabled={busy}
                    onClear={() => setVision(null)}
                  />
                ) : undefined
              }
            >
              <ProfileModelSelect
                id="profile-vision"
                labelledBy="profile-vision-label"
                describedBy="profile-vision-hint"
                groups={visionGroups}
                value={pointerValue(vision)}
                disabled={busy || !canChooseVision}
                followLabel="不配置"
                onChange={(value) => setVision(decodePointer(value))}
              />
            </SettingField>
          </div>
        )}
      </div>

      <SettingsFormMessage>{saveError}</SettingsFormMessage>

      {!saveError && saveWarnings && saveWarnings.length > 0 ? (
        <ProfileSaveWarnings warnings={saveWarnings} />
      ) : null}

      <div className="flex justify-end gap-2 border-t border-border pt-3">
        <Button variant="neutral" size="md" disabled={busy} onClick={onCancel}>
          取消
        </Button>
        <Button
          size="md"
          disabled={busy || !main}
          icon={
            busy ? <Loader2 size={14} className="animate-spin" /> : undefined
          }
          onClick={() => void handleSave()}
        >
          保存
        </Button>
      </div>
    </div>
  );
}
