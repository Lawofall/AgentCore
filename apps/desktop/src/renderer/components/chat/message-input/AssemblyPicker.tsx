import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  patchConversationCache,
  useConversations,
} from "@/hooks/useConversations";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import { assemblyPickerBlurb } from "@/lib/assemblyLock";
import { useChatPaneId } from "@/lib/chatPane";
import {
  lookupComposerProfile,
  resolveComposerProfileId,
  useComposerProfileDraftStore,
} from "@/lib/composerModelProfile";
import { llmModelProfileKeys } from "@/lib/queryKeys";
import { notifyError } from "@/lib/toast";
import { setConversationModelProfile } from "@/services/conversations";
import {
  type LlmModelProfileView,
  rememberAssemblySelection,
  resolveDefaultProfile,
} from "@/services/llmModelProfiles";
import { getLastUsedAssemblyId } from "@/services/models";
import type { Conversation } from "@/stores/conversation";
import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Layers, Loader2, Settings2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ComposerPlusBackHeader, useComposerPlusRow } from "./ComposerPlusMenu";

/**
 * 输入框「能力」芯片 — 只换这场钉的能力（工具、信封、交代、插头）。模型另在模型芯片。
 * 已有会话 `PATCH … assembly_id`；新会话记草稿，首发带上这个 id。
 * 人选一份就记 last-used，并写入账号回落；列表不标「默认」。
 */

function AssemblyRow({
  row,
  selected,
  onPick,
}: {
  row: LlmModelProfileView;
  selected: boolean;
  onPick: (id: string) => void;
}) {
  const blurb = assemblyPickerBlurb(row.recipe);
  return (
    <button
      type="button"
      onClick={() => onPick(row.id)}
      aria-label={row.name}
      aria-current={selected ? "true" : undefined}
      className={`flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left ${
        selected ? "bg-primary/10" : "hover:bg-accent/50"
      }`}
    >
      <span className="mr-auto flex min-w-0 flex-col gap-0.5">
        <span className="truncate text-sm text-foreground">{row.name}</span>
        {blurb ? (
          <span className="text-xs text-muted-foreground">{blurb}</span>
        ) : null}
      </span>
      {selected && <Check size={14} className="mt-0.5 shrink-0 text-primary" />}
    </button>
  );
}

export function AssemblyPicker({ disabled }: { disabled?: boolean }) {
  const conversationId = useChatPaneId();
  const conversations = useConversations();
  const {
    data: profileList,
    isLoading,
    isError,
    refetch,
  } = useLlmModelProfiles();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const plus = useComposerPlusRow("assembly");
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const draftId = useComposerProfileDraftStore((s) => s.assemblyId);
  const setDraftId = useComposerProfileDraftStore((s) => s.setAssemblyId);

  // biome-ignore lint/correctness/useExhaustiveDependencies: conversationId is the reset key
  useEffect(() => {
    setDraftId(null);
  }, [conversationId, setDraftId]);

  const rows = profileList?.data ?? [];
  const accountDefault = resolveDefaultProfile(profileList);
  const activeConv = conversationId
    ? conversations.find((c: Conversation) => c.id === conversationId)
    : undefined;
  const overrideId = activeConv?.assemblyId?.trim() || null;
  const selectedId = resolveComposerProfileId({
    conversationId,
    conversationProfileId: overrideId,
    draftProfileId: draftId,
    lastUsedProfileId: getLastUsedAssemblyId(),
    profileIds: rows.map((row) => row.id),
  });
  const highlightId = selectedId ?? accountDefault?.id ?? null;
  const display = useMemo(
    () => lookupComposerProfile(selectedId, rows, accountDefault),
    [selectedId, rows, accountDefault],
  );
  const visible = useMemo(
    () =>
      rows.filter(
        (row) =>
          row.kind === "system" ||
          row.kind === "user" ||
          (row.kind === "implicit" && row.id === overrideId),
      ),
    [rows, overrideId],
  );
  const systemRows = visible.filter((row) => row.kind === "system");
  const userRows = visible.filter(
    (row) => row.kind === "user" || row.kind === "implicit",
  );

  const apply = async (assemblyId: string) => {
    if (disabled || pending) return;
    if (plus.mode === "panel" || plus.mode === "row") plus.close();
    else setOpen(false);
    if (!conversationId) {
      setDraftId(assemblyId);
      try {
        await rememberAssemblySelection(assemblyId);
        void queryClient.invalidateQueries({
          queryKey: llmModelProfileKeys.list,
        });
      } catch (e) {
        notifyError(e, "切换能力失败");
      }
      return;
    }
    setPending(true);
    try {
      const saved = await setConversationModelProfile(
        conversationId,
        assemblyId,
      );
      patchConversationCache(conversationId, {
        assemblyId: saved.assemblyId ?? null,
      });
      await rememberAssemblySelection(assemblyId);
      void queryClient.invalidateQueries({
        queryKey: llmModelProfileKeys.list,
      });
    } catch (e) {
      notifyError(e, "切换能力失败");
    } finally {
      setPending(false);
    }
  };

  if (plus.mode === "hidden") return null;

  if (isLoading && !display) {
    return (
      <span className="inline-flex h-8 items-center gap-1 px-2 text-sm text-muted-foreground">
        <Loader2 size={14} className="animate-spin" />
      </span>
    );
  }

  const label = display?.name ?? "选择能力";
  const dismiss = () => {
    if (plus.mode === "panel" || plus.mode === "row") plus.close();
    else setOpen(false);
  };
  const panel = (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto p-1">
        {isError ? (
          <div className="px-2.5 py-3 text-xs">
            <p className="text-muted-foreground">加载能力失败</p>
            <button
              type="button"
              onClick={() => void refetch()}
              className="mt-1 text-primary hover:underline"
            >
              重试
            </button>
          </div>
        ) : visible.length === 0 ? (
          <p className="px-2.5 py-4 text-xs text-muted-foreground">
            还没有能力
          </p>
        ) : (
          <>
            {systemRows.map((row) => (
              <AssemblyRow
                key={row.id}
                row={row}
                selected={highlightId === row.id}
                onPick={apply}
              />
            ))}
            {userRows.map((row) => (
              <AssemblyRow
                key={row.id}
                row={row}
                selected={highlightId === row.id}
                onPick={apply}
              />
            ))}
          </>
        )}
      </div>
      <button
        type="button"
        onClick={() => {
          dismiss();
          navigate("/toolbox/overview");
        }}
        className="flex items-center gap-1.5 border-t border-border px-2.5 py-2 text-left text-xs text-primary hover:bg-accent/40"
      >
        <Settings2 size={13} className="shrink-0" />
        管理能力…
      </button>
    </>
  );
  const trigger = (
    <button
      type="button"
      disabled={disabled || pending}
      aria-label={`能力：${label}`}
      aria-expanded={plus.mode === "panel" || open}
      onClick={plus.mode === "row" ? plus.drill : undefined}
      className={`inline-flex h-8 items-center gap-1 rounded-lg px-2 text-sm text-muted-foreground hover:bg-accent/60 hover:text-foreground ${
        plus.mode === "row" ? "w-full max-w-none justify-start" : "max-w-40"
      } ${disabled || pending ? "cursor-not-allowed opacity-60" : ""}`}
    >
      {pending ? (
        <Loader2 size={14} className="shrink-0 animate-spin" />
      ) : (
        <Layers size={14} className="shrink-0" />
      )}
      <span className="truncate">{label}</span>
      <ChevronDown size={12} className="shrink-0 opacity-60" />
    </button>
  );

  if (plus.mode === "panel") {
    return (
      <div className="flex max-h-[22rem] w-72 flex-col">
        <ComposerPlusBackHeader title="能力" onBack={plus.back} />
        {panel}
      </div>
    );
  }
  if (plus.mode === "row") {
    return trigger;
  }
  return (
    <div className="relative shrink-0">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>{trigger}</PopoverTrigger>
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
