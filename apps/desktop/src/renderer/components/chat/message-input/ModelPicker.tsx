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
import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import { useChatPaneId } from "@/lib/chatPane";
import {
  type ConversationModelOverride,
  completeModelOverride,
  putComposerModel,
  slotsFromProfile,
  useComposerActiveProfile,
  useComposerProfileDraftStore,
} from "@/lib/composerModelProfile";
import { isModelPickerFloatTarget } from "@/lib/modelPicker";
import { notifyError } from "@/lib/toast";
import { type ProfileDraft, ProfileEditor } from "@/pages/more/ModelSettings";
import { setConversationModelOverride } from "@/services/conversations";
import { slotDisplayName } from "@/services/llmModelProfiles";
import { resolvedProfileEffort } from "@/services/models";
import type { Conversation } from "@/stores/conversation";
import { ChevronDown, Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ComposerPlusBackHeader, useComposerPlusRow } from "./ComposerPlusMenu";

/**
 * 输入框模型芯片 — 只改这场的脑子，不换装配。
 * 写名字；有思考档位时跟在后面。不写倍率。
 * 已有会话 PATCH model_override；新会话记草稿，首发带上。
 */

export function draftFromOverride(
  override: ConversationModelOverride,
): ProfileDraft {
  return {
    name: "",
    main: override.main,
    worker: override.worker ?? null,
    reasoning_effort: override.reasoning_effort ?? null,
    context_budget: override.context_budget ?? null,
  };
}

export function useComposerModelPickerSession(disabled?: boolean) {
  const conversationId = useChatPaneId();
  const conversations = useConversations();
  const modelsQuery = useModels();
  const providersQuery = useLlmProviders();
  const plus = useComposerPlusRow("model");
  const [pending, setPending] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const setDraftOverride = useComposerProfileDraftStore(
    (s) => s.setModelOverride,
  );
  const profile = useComposerActiveProfile();
  const catalog = modelsQuery.data;
  const catalogModels = catalog?.models ?? [];
  const providers = providersQuery.data?.providers ?? [];
  const platformAvailable = providersQuery.data?.platform_available === true;
  const initial = useMemo(() => slotsFromProfile(profile), [profile]);
  const name = profile?.main
    ? slotDisplayName(profile.main, catalogModels) || profile.main.model
    : "选择模型";
  const effort =
    profile?.main != null
      ? resolvedProfileEffort(
          { main: profile.main, reasoning_effort: profile.reasoning_effort },
          catalogModels,
        )
      : null;
  const label = effort ? `${name} · ${effort}` : name;

  const apply = async (draft: ProfileDraft) => {
    if (!draft.main || disabled) return;
    const override = completeModelOverride({
      main: draft.main,
      worker: draft.worker,
      reasoning_effort: draft.reasoning_effort,
      context_budget: draft.context_budget,
    });
    setDraftOverride(override);
    setPending(true);
    try {
      const account = putComposerModel(override);
      if (!conversationId) {
        await account;
        setWarnings([]);
        return;
      }
      const [saved] = await Promise.all([
        setConversationModelOverride(conversationId, override),
        account,
      ]);
      const conv = conversations.find(
        (row: Conversation) => row.id === conversationId,
      );
      patchConversationCache(conversationId, {
        assemblyId: conv?.assemblyId ?? saved.assemblyId ?? null,
        modelOverride: completeModelOverride(saved.modelOverride ?? override),
      });
      setWarnings([]);
    } catch (e) {
      notifyError(e, "切换模型失败");
    } finally {
      setPending(false);
    }
  };

  return {
    plus,
    conversationId,
    profile,
    initial,
    name,
    effort,
    label,
    apply,
    pending,
    warnings,
    catalog,
    catalogModels,
    providers,
    platformAvailable,
    loading: providersQuery.isLoading,
  };
}

export function ModelPicker({ disabled }: { disabled?: boolean }) {
  const session = useComposerModelPickerSession(disabled);
  const {
    plus,
    conversationId,
    profile,
    initial,
    name,
    effort,
    label,
    apply,
    pending,
    warnings,
    catalog,
    catalogModels,
    providers,
    platformAvailable,
    loading,
  } = session;
  const [open, setOpen] = useState(false);

  if (plus.mode === "hidden") return null;

  if (!profile && loading) {
    return (
      <span className="inline-flex h-8 items-center gap-1 px-2 text-xs text-muted-foreground">
        <Loader2 size={14} className="animate-spin" />
      </span>
    );
  }

  const dismiss = () => {
    if (plus.mode === "panel" || plus.mode === "row") plus.close();
    else setOpen(false);
  };

  const editor = initial ? (
    <ProfileEditor
      // 只跟这场重挂。把模型 id 写进 key 会在手填每个字符时拆掉输入框。
      key={conversationId ?? "draft"}
      title=""
      showTitle={false}
      showCancel={false}
      framed={false}
      surface="shelf"
      shelfLayout={plus.mode === "panel" ? "stack" : "columns"}
      providers={providers}
      catalog={catalog}
      catalogModels={catalogModels}
      initial={draftFromOverride(initial)}
      saveWarnings={warnings}
      pending={pending}
      onCancel={() => undefined}
      onSave={apply}
    />
  ) : (
    <p className="px-2.5 py-4 text-xs text-muted-foreground">
      暂无可用模型。
      {platformAvailable ? (
        <>请稍后重试。</>
      ) : (
        <>
          请先到{" "}
          <Link
            to="/more/providers"
            onClick={dismiss}
            className="text-primary underline-offset-2 hover:underline"
          >
            接入服务商
          </Link>
        </>
      )}
    </p>
  );

  const trigger = (
    <button
      type="button"
      disabled={disabled || pending}
      aria-label={`模型：${label}`}
      aria-expanded={plus.mode === "panel" || open}
      onClick={plus.mode === "row" ? plus.drill : undefined}
      className={`inline-flex h-8 max-w-64 items-center gap-1 rounded-lg px-2 text-xs text-foreground hover:bg-accent/60 ${
        disabled || pending ? "cursor-not-allowed opacity-60" : ""
      }`}
    >
      {pending ? <Loader2 size={14} className="shrink-0 animate-spin" /> : null}
      <span className="truncate">{name}</span>
      {effort ? (
        <span className="shrink-0 text-muted-foreground">{effort}</span>
      ) : null}
      <ChevronDown size={12} className="shrink-0 text-muted-foreground" />
    </button>
  );

  if (plus.mode === "panel") {
    return (
      <div className="flex max-h-[22rem] w-80 flex-col">
        <ComposerPlusBackHeader title="模型" onBack={plus.back} />
        <div className="min-h-0 flex-1 overflow-y-auto">{editor}</div>
      </div>
    );
  }
  if (plus.mode === "row") {
    return (
      <SimpleTooltip label={`换这场用的模型：${label}`}>
        {trigger}
      </SimpleTooltip>
    );
  }
  return (
    <div className="relative shrink-0">
      <Popover open={open} onOpenChange={setOpen}>
        <SimpleTooltip label={`换这场用的模型：${label}`}>
          <PopoverTrigger asChild>{trigger}</PopoverTrigger>
        </SimpleTooltip>
        <PopoverContent
          side="top"
          align="end"
          collisionPadding={8}
          onCloseAutoFocus={(e) => e.preventDefault()}
          onInteractOutside={(e) => {
            if (isModelPickerFloatTarget(e.target)) {
              e.preventDefault();
            }
          }}
          className="w-[min(13rem,calc(100vw-2rem))] overflow-hidden p-0"
        >
          {editor}
        </PopoverContent>
      </Popover>
    </div>
  );
}
