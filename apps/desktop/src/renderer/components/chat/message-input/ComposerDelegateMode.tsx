import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  patchConversationCache,
  useConversations,
} from "@/hooks/useConversations";
import { useChatPaneId } from "@/lib/chatPane";
import {
  getComposerDraftDelegateMode,
  rememberDelegateSelection,
  setComposerDraftDelegateMode,
  subscribeComposerDraftDelegateMode,
} from "@/lib/composerDelegateMode";
import { notifyError } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { setConversationDelegateMode } from "@/services/conversations";
import { Workflow } from "lucide-react";
import { useState, useSyncExternalStore } from "react";
import { useComposerPlusHost } from "./ComposerPlusMenu";

/**
 * Composer「统筹」：空草稿贴能力；有会话后在「更多」里。
 * 开 = 优先派；关 = 现行注意力尺。写在这场；新对话跟上次选用。正在生成的这一轮不换。
 * 极简时灰掉（这场只聊天，派不出队员）。
 * 世界行幽灵芯片；关着写「· 关」，开着字变实色、词后一颗绿点，不铺底。
 */
export function ComposerDelegateMode({
  disabled,
  chatOnly = false,
}: {
  disabled?: boolean;
  /** 极简：这场只聊天，不能优先派。 */
  chatOnly?: boolean;
}) {
  const conversationId = useChatPaneId();
  const plusHost = useComposerPlusHost();
  const blocked = Boolean(disabled || chatOnly);
  const conversations = useConversations();
  const draft = useSyncExternalStore(
    subscribeComposerDraftDelegateMode,
    getComposerDraftDelegateMode,
    getComposerDraftDelegateMode,
  );
  const [pending, setPending] = useState(false);
  const fromCache = conversationId
    ? Boolean(
        conversations.find((row) => row.id === conversationId)?.delegateMode,
      )
    : draft;
  const on = fromCache;

  const apply = async () => {
    if (pending || blocked) return;
    const next = !on;
    if (!conversationId) {
      setComposerDraftDelegateMode(next);
      return;
    }
    setPending(true);
    patchConversationCache(conversationId, { delegateMode: next });
    try {
      await setConversationDelegateMode(conversationId, next);
      rememberDelegateSelection(next);
    } catch (err) {
      patchConversationCache(conversationId, { delegateMode: on });
      notifyError(err, "切换统筹失败");
    } finally {
      setPending(false);
    }
  };

  const pressed = on && !chatOnly;
  if (plusHost && plusHost.panel !== "list") return null;
  const tip = chatOnly
    ? "这场只聊天，不能优先派。"
    : "适合复杂任务。开启统筹后，主 Agent 专注于规划与任务拆解；具体执行交由子 Agent 完成。该模式可显著降低整体成本与耗时，同时保持良好输出质量。（建议主Agent用gpt6.1等模型、子Agent用deepseek，需自行配置）";

  return (
    <SimpleTooltip label={tip}>
      <button
        type="button"
        role="switch"
        disabled={blocked || pending}
        aria-checked={pressed}
        aria-label="统筹"
        onClick={() => void apply()}
        className={cn(
          "inline-flex h-8 shrink-0 items-center gap-1 rounded-lg px-2 text-sm hover:bg-accent/60",
          plusHost ? "w-full max-w-none justify-start" : "",
          pressed
            ? "text-foreground"
            : "text-muted-foreground hover:text-foreground",
          blocked || pending ? "cursor-not-allowed opacity-60" : "",
        )}
      >
        <Workflow size={14} className="shrink-0" />
        统筹
        {pressed ? (
          <span
            className="inline-block size-1.5 shrink-0 rounded-full bg-success"
            aria-hidden
          />
        ) : chatOnly ? null : (
          <span aria-hidden>· 关</span>
        )}
      </button>
    </SimpleTooltip>
  );
}
