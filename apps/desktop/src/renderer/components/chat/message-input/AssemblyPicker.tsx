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
import { foldedIntoPreset } from "@/lib/assemblyFold";
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
  resolveDefaultProfile,
} from "@/services/llmModelProfiles";
import {
  getLastUsedAssemblyId,
  setLastUsedAssemblyId,
} from "@/services/models";
import type { Conversation } from "@/stores/conversation";
import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Layers, Loader2, Settings2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ComposerPlusBackHeader, useComposerPlusRow } from "./ComposerPlusMenu";

/**
 * 输入框「装配」芯片 — 只换这场钉的装配（模型、工具、信封、交代、插头）。
 * 已有会话 `PATCH … assembly_id`；新会话记草稿，首发带上这个 id。
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
  return (
    <button
      type="button"
      onClick={() => onPick(row.id)}
      aria-current={selected ? "true" : undefined}
      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left ${
        selected ? "bg-primary/10" : "hover:bg-accent/50"
      }`}
    >
      <div className="mr-auto flex min-w-0 items-center gap-1.5">
        <span className="truncate text-sm text-foreground">{row.name}</span>
        {row.is_default && (
          <span className="shrink-0 rounded bg-primary/10 px-1 py-0.5 text-xs text-primary">
            默认
          </span>
        )}
      </div>
      {selected && <Check size={14} className="shrink-0 text-primary" />}
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
  const active = rows.find((row) => row.id === highlightId) ?? display;
  const foldedPreset = active
    ? foldedIntoPreset(active, systemRows)
    : undefined;
  const userRows = visible.filter(
    (row) =>
      (row.kind === "user" || row.kind === "implicit") &&
      !foldedIntoPreset(row, systemRows),
  );

  const apply = async (assemblyId: string) => {
    if (disabled || pending) return;
    setLastUsedAssemblyId(assemblyId);
    if (plus.mode === "panel" || plus.mode === "row") plus.close();
    else setOpen(false);
    if (!conversationId) {
      setDraftId(assemblyId);
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
      void queryClient.invalidateQueries({
        queryKey: llmModelProfileKeys.list,
      });
    } catch (e) {
      notifyError(e, "切换装配失败");
    } finally {
      setPending(false);
    }
  };

  if (plus.mode === "hidden") return null;

  if (isLoading && !display) {
    return (
      <span className="inline-flex h-8 items-center gap-1 px-2 text-xs text-muted-foreground">
        <Loader2 size={14} className="animate-spin" />
      </span>
    );
  }

  const label = display?.name ?? "选择装配";
  const dismiss = () => {
    if (plus.mode === "panel" || plus.mode === "row") plus.close();
    else setOpen(false);
  };
  const panel = (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto p-1">
        {isError ? (
          <div className="px-2.5 py-3 text-xs">
            <p className="text-muted-foreground">加载装配失败</p>
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
            还没有装配
          </p>
        ) : (
          <>
            {systemRows.map((row) => {
              const covers = visible.filter(
                (item) => foldedIntoPreset(item, systemRows)?.id === row.id,
              );
              const face =
                !row.is_default && covers.some((item) => item.is_default)
                  ? { ...row, is_default: true }
                  : row;
              return (
                <AssemblyRow
                  key={row.id}
                  row={face}
                  selected={
                    highlightId === row.id || foldedPreset?.id === row.id
                  }
                  onPick={apply}
                />
              );
            })}
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
        管理装配…
      </button>
    </>
  );
  const trigger = (
    <button
      type="button"
      disabled={disabled || pending}
      aria-label={`装配：${label}`}
      aria-expanded={plus.mode === "panel" || open}
      onClick={plus.mode === "row" ? plus.drill : undefined}
      className={`inline-flex h-8 max-w-40 items-center gap-1 rounded-lg px-2 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground ${
        disabled || pending ? "cursor-not-allowed opacity-60" : ""
      }`}
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
        <ComposerPlusBackHeader title="装配" onBack={plus.back} />
        {panel}
      </div>
    );
  }
  if (plus.mode === "row") {
    return (
      <SimpleTooltip label={`换这场用的装配，含模型：${label}`}>
        {trigger}
      </SimpleTooltip>
    );
  }
  return (
    <div className="relative shrink-0">
      <Popover open={open} onOpenChange={setOpen}>
        <SimpleTooltip label={`换这场用的装配，含模型：${label}`}>
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
