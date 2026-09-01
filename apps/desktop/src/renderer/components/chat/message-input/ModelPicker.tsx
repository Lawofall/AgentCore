import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  patchConversationCache,
  useConversations,
} from "@/hooks/useConversations";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import {
  lookupComposerProfile,
  resolveComposerProfileId,
  useComposerProfileDraftStore,
} from "@/lib/composerModelProfile";
import { notifyError } from "@/lib/toast";
import { setConversationModelProfile } from "@/services/conversations";
import {
  type LlmModelProfileView,
  profileSlotSummary,
  resolveDefaultProfile,
} from "@/services/llmModelProfiles";
import { getLastUsedProfileId, setLastUsedProfileId } from "@/services/models";
import type { Conversation } from "@/stores/conversation";
import { useConversationStore } from "@/stores/conversation";
import {
  Bot,
  Check,
  ChevronDown,
  Layers,
  Loader2,
  Settings2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ComposerPlusBackHeader, useComposerPlusRow } from "./ComposerPlusMenu";

/**
 * 输入框「模型组合」选择器 — 只选具体组合，不做裸模型列表。
 *
 * 数据源：`GET /v1/users/me/llm-model-profiles`。选择即写：已有会话
 * `PATCH … model_profile_id`；新会话先记草稿 + last_profile_id，首发
 * `POST /v1/conversations` 带 `model_profile_id` 拍快照。触发器与下拉都只显示
 * 组合名（与同排徽章等高）+ 系统预置的「预置」徽章；主 · Worker 摘要只在
 * chip tooltip，不占下拉行。
 */

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2.5 pt-1.5 pb-0.5 text-xs font-medium text-muted-foreground">
      {children}
    </div>
  );
}

/** 「预置」标识：下拉选项行与折叠态 chip 共用，勿各写一套。 */
function PresetBadge() {
  return (
    <span className="shrink-0 rounded bg-muted px-1 py-0.5 text-xs text-muted-foreground">
      预置
    </span>
  );
}

function ProfileRow({
  profile,
  selected,
  onPick,
}: {
  profile: LlmModelProfileView;
  selected: boolean;
  onPick: (id: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onPick(profile.id)}
      aria-current={selected ? "true" : undefined}
      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left ${
        selected ? "bg-primary/10" : "hover:bg-accent/50"
      }`}
    >
      <div className="mr-auto flex min-w-0 items-center gap-1.5">
        <span className="truncate text-sm text-foreground">{profile.name}</span>
        {profile.is_default && (
          <span className="shrink-0 rounded bg-primary/10 px-1 py-0.5 text-xs text-primary">
            默认
          </span>
        )}
        {profile.kind === "system" && <PresetBadge />}
      </div>
      {selected && <Check size={14} className="shrink-0 text-primary" />}
    </button>
  );
}

/** Profiles shown in the picker: system + user (+ current implicit if attached). */
function pickerProfiles(
  all: LlmModelProfileView[],
  attachedId: string | null | undefined,
): LlmModelProfileView[] {
  return all.filter(
    (p) =>
      p.kind === "system" ||
      p.kind === "user" ||
      (p.kind === "implicit" && p.id === attachedId),
  );
}

export function ModelPicker({ disabled }: { disabled?: boolean }) {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const conversations = useConversations();
  const {
    data: profileList,
    isLoading,
    isError,
    refetch,
  } = useLlmModelProfiles();
  const { data: catalog } = useModels();
  const { data: providersResponse } = useLlmProviders();
  const platformAvailable = providersResponse?.platform_available === true;
  const navigate = useNavigate();

  const plus = useComposerPlusRow("model");
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const draftProfileId = useComposerProfileDraftStore((s) => s.profileId);
  const setDraftProfileId = useComposerProfileDraftStore((s) => s.setProfileId);

  // biome-ignore lint/correctness/useExhaustiveDependencies: conversationId is the reset key
  useEffect(() => {
    setDraftProfileId(null);
  }, [conversationId, setDraftProfileId]);

  const catalogModels = useMemo(() => catalog?.models ?? [], [catalog]);
  const profiles = profileList?.data ?? [];
  const accountDefault = resolveDefaultProfile(profileList);

  const activeConv = conversationId
    ? conversations.find((c: Conversation) => c.id === conversationId)
    : undefined;
  const overrideId = activeConv?.modelProfileId?.trim() || null;

  const selectedId = resolveComposerProfileId({
    conversationId,
    conversationProfileId: overrideId,
    draftProfileId,
    lastUsedProfileId: getLastUsedProfileId(),
    profileIds: profiles.map((p) => p.id),
  });
  /** Highlight which row is active; fall back to account default when none chosen yet. */
  const highlightId = selectedId ?? accountDefault?.id ?? null;

  const displayProfile = useMemo(
    () => lookupComposerProfile(selectedId, profiles, accountDefault),
    [selectedId, profiles, accountDefault],
  );

  const visibleProfiles = useMemo(
    () => pickerProfiles(profiles, overrideId),
    [profiles, overrideId],
  );

  const systemProfiles = visibleProfiles.filter((p) => p.kind === "system");
  const userProfiles = visibleProfiles.filter(
    (p) => p.kind === "user" || p.kind === "implicit",
  );

  const summary = displayProfile
    ? profileSlotSummary(displayProfile, catalogModels)
    : "";

  const applyProfile = async (profileId: string) => {
    if (disabled || pending) return;
    setLastUsedProfileId(profileId);
    if (plus.mode === "panel" || plus.mode === "row") plus.close();
    else setOpen(false);
    if (!conversationId) {
      setDraftProfileId(profileId);
      return;
    }
    setPending(true);
    try {
      const saved = await setConversationModelProfile(
        conversationId,
        profileId,
      );
      patchConversationCache(conversationId, {
        modelProfileId: saved.modelProfileId ?? null,
      });
    } catch (e) {
      notifyError(e, "切换模型组合失败");
    } finally {
      setPending(false);
    }
  };

  if (plus.mode === "hidden") return null;

  if (isLoading && !displayProfile) {
    return (
      <span className="inline-flex h-8 items-center gap-1 px-2 text-xs text-muted-foreground">
        <Loader2 size={14} className="animate-spin" />
      </span>
    );
  }

  const label = displayProfile?.name ?? "选择组合";
  // 折叠态就要看出是平台预置：不展开下拉误以为跑的是自配组合，是线上报障来源。
  const isPreset = displayProfile?.kind === "system";
  const hint = "切换本会话使用的模型组合（当前回合起生效）";
  // 单行 chip 与同排徽章对齐，主·Worker 摘要只在 tooltip。
  // 组合名领衔：chip 宽度容不下带档位后缀的预置名（「… · 免费额度」必被截掉），
  // 而免费档与付费档的裸名一字不差，截断处正好是唯一能分辨两者的地方。
  const tooltip = (
    <span className="flex flex-col gap-0.5">
      <span className="font-medium">{label}</span>
      <span>{hint}</span>
      {summary && <span className="opacity-70">{summary}</span>}
    </span>
  );

  const dismiss = () => {
    if (plus.mode === "panel" || plus.mode === "row") plus.close();
    else setOpen(false);
  };

  const panel = (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto p-1">
        {isError ? (
          <div className="px-2.5 py-3 text-xs">
            <p className="text-muted-foreground">加载模型组合失败</p>
            <button
              type="button"
              onClick={() => void refetch()}
              className="mt-1 text-primary hover:underline"
            >
              重试
            </button>
          </div>
        ) : visibleProfiles.length === 0 ? (
          <div className="px-2.5 py-4 text-xs text-muted-foreground">
            <p>暂无可用组合</p>
            {platformAvailable ? (
              <p className="mt-1">
                请稍后重试，或到{" "}
                <Link
                  to="/more/model"
                  onClick={dismiss}
                  className="text-primary underline-offset-2 hover:underline"
                >
                  设置 · 模型
                </Link>{" "}
                检查配置。
              </p>
            ) : (
              <p className="mt-1">
                请先到{" "}
                <Link
                  to="/more/providers"
                  onClick={dismiss}
                  className="text-primary underline-offset-2 hover:underline"
                >
                  接入服务商
                </Link>
              </p>
            )}
          </div>
        ) : (
          <>
            {systemProfiles.length > 0 && (
              <div>
                <GroupLabel>系统预置</GroupLabel>
                {systemProfiles.map((p) => (
                  <ProfileRow
                    key={p.id}
                    profile={p}
                    selected={highlightId === p.id}
                    onPick={applyProfile}
                  />
                ))}
              </div>
            )}

            {userProfiles.length > 0 && (
              <div>
                <GroupLabel>我的组合</GroupLabel>
                {userProfiles.map((p) => (
                  <ProfileRow
                    key={p.id}
                    profile={p}
                    selected={highlightId === p.id}
                    onPick={applyProfile}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>

      <button
        type="button"
        onClick={() => {
          dismiss();
          navigate("/more/model");
        }}
        className="flex items-center gap-1.5 border-t border-border px-2.5 py-2 text-left text-xs text-primary hover:bg-accent/40"
      >
        <Settings2 size={13} className="shrink-0" />
        管理组合…
        <Layers size={12} className="ml-auto shrink-0 opacity-60" />
      </button>
    </>
  );

  const trigger = (
    <button
      type="button"
      disabled={disabled || pending}
      aria-label={`模型组合：${label}${isPreset ? "（预置）" : ""}`}
      aria-expanded={plus.mode === "panel" || open}
      onClick={plus.mode === "row" ? plus.drill : undefined}
      className={`inline-flex h-8 max-w-40 items-center gap-1 rounded-lg px-2 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground ${
        disabled || pending ? "cursor-not-allowed opacity-60" : ""
      }`}
    >
      {pending ? (
        <Loader2 size={14} className="shrink-0 animate-spin" />
      ) : (
        <Bot size={14} className="shrink-0" />
      )}
      <span className="truncate">{label}</span>
      {isPreset && <PresetBadge />}
      <ChevronDown size={12} className="shrink-0 opacity-60" />
    </button>
  );

  if (plus.mode === "panel") {
    return (
      <div className="flex max-h-[22rem] w-72 flex-col">
        <ComposerPlusBackHeader title="模型组合" onBack={plus.back} />
        {panel}
      </div>
    );
  }

  if (plus.mode === "row") {
    return <SimpleTooltip label={tooltip}>{trigger}</SimpleTooltip>;
  }

  return (
    <div className="relative shrink-0">
      <Popover open={open} onOpenChange={setOpen}>
        <SimpleTooltip label={tooltip}>
          <PopoverTrigger asChild>{trigger}</PopoverTrigger>
        </SimpleTooltip>
        <PopoverContent
          side="bottom"
          align="start"
          avoidCollisions={false}
          onCloseAutoFocus={(e) => e.preventDefault()}
          className="flex max-h-[22rem] w-max min-w-52 max-w-72 flex-col p-0"
        >
          {panel}
        </PopoverContent>
      </Popover>
    </div>
  );
}
