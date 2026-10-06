import { useChatPaneScope } from "@/lib/chatPaneContext";
import { DRAFT_KEY, useConversationStore } from "@/stores/conversation";

export { ChatPaneProvider } from "@/lib/chatPaneContext";

/** 这一栏的对话。栏外等于当前焦点。 */
export function useChatPaneId(): string | null {
  const scoped = useChatPaneScope();
  const current = useConversationStore((s) => s.currentConversationId);
  return scoped === undefined ? current : scoped;
}

/**
 * 切片键。草稿栏必须是 {@link DRAFT_KEY}，不能把 `null` 再落回焦点栏
 * （`runtimeOf` 会把空 id 当成「跟当前焦点」）。
 */
export function useChatPaneSliceKey(): string {
  const id = useChatPaneId();
  return id ?? DRAFT_KEY;
}
