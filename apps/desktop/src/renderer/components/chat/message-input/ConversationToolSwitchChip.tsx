import { ToolSwitchList } from "@/components/tools/ToolSwitchList";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useConversations } from "@/hooks/useConversations";
import { useChatPaneId } from "@/lib/chatPane";
import { conversationToolDiffLabel } from "@/lib/toolSwitchDiff";
import { cn } from "@/lib/utils";
import {
  getAccountToolSwitchboard,
  getComposerDraftDisabledTools,
  loadAccountToolSwitches,
  setComposerDraftDisabledTools,
  subscribeAccountToolSwitches,
  subscribeComposerDraftDisabledTools,
} from "@/services/toolSwitches";
import { ChevronDown, Wrench } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { ComposerPlusBackHeader, useComposerPlusRow } from "./ComposerPlusMenu";

/**
 * Composer tool deny list. Same shape as the permission badge and the model
 * picker: this conversation (or the blank draft), with 「设为新会话默认」
 * as the only account write.
 */
export function ConversationToolSwitchChip() {
  const conversationId = useChatPaneId();
  const conversations = useConversations();
  const account = useSyncExternalStore(
    subscribeAccountToolSwitches,
    getAccountToolSwitchboard,
    () => null,
  );
  const draft = useSyncExternalStore(
    subscribeComposerDraftDisabledTools,
    getComposerDraftDisabledTools,
    () => null,
  );
  const plus = useComposerPlusRow("tools");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (account) return;
    void loadAccountToolSwitches().catch(() => {});
  }, [account]);

  useEffect(() => {
    if (!conversationId) return;
    setComposerDraftDisabledTools(null);
  }, [conversationId]);

  const conversation = conversationId
    ? conversations.find((row) => row.id === conversationId)
    : undefined;
  const effective = conversationId
    ? conversation?.disabledTools
    : (draft ?? account?.disabled);
  const diff =
    account && effective
      ? conversationToolDiffLabel(effective, account.disabled)
      : null;
  const visible = diff ?? "工具";
  const readOnly = conversation?.permissionAxes?.boundary === "read";
  const hint = conversationId
    ? "改这场要用的工具。正在生成的这一轮不换。"
    : "改即将新建的这场要用的工具。";

  if (plus.mode === "hidden") return null;

  const panel = (
    <>
      {plus.mode !== "panel" ? (
        <p className="px-1 pb-1.5 text-xs font-medium text-muted-foreground">
          工具
        </p>
      ) : null}
      <ToolSwitchList
        scope={conversationId ? "conversation" : "draft"}
        conversationId={conversationId ?? undefined}
        conversationTitle={conversation?.title}
        readOnlyBoundary={readOnly}
      />
    </>
  );

  const trigger = (
    <button
      type="button"
      data-testid="conversation-tool-switch"
      aria-label={diff ? `工具：${diff}` : "工具"}
      aria-expanded={plus.mode === "panel" || open}
      onClick={plus.mode === "row" ? plus.drill : undefined}
      className={cn(
        "inline-flex h-8 max-w-44 items-center gap-1 rounded-lg px-2 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground",
      )}
    >
      <Wrench size={14} className="shrink-0" />
      <span className="truncate">{visible}</span>
      <ChevronDown size={12} className="shrink-0 opacity-60" />
    </button>
  );

  if (plus.mode === "panel") {
    return (
      <div className="w-80 p-0">
        <ComposerPlusBackHeader title="工具" onBack={plus.back} />
        <div className="p-2">{panel}</div>
      </div>
    );
  }

  if (plus.mode === "row") {
    return <SimpleTooltip label={hint}>{trigger}</SimpleTooltip>;
  }

  return (
    <div className="relative shrink-0">
      <Popover open={open} onOpenChange={setOpen}>
        <SimpleTooltip label={hint}>
          <PopoverTrigger asChild>{trigger}</PopoverTrigger>
        </SimpleTooltip>
        <PopoverContent
          side="bottom"
          align="start"
          avoidCollisions={false}
          onCloseAutoFocus={(e) => e.preventDefault()}
          className="w-80 p-2"
        >
          {open ? panel : null}
        </PopoverContent>
      </Popover>
    </div>
  );
}
