import { useChatPaneScope } from "@/lib/chatPaneContext";
import { type ErrorAction, visibleMessageText } from "@/lib/errors";
import { precedingUserMessageId } from "@/lib/supportDiagnostics";
import type { ProcessStep } from "@/types/events";
import {
  DRAFT_KEY,
  activeRuntime,
  lastAssistantMessageId,
  lastAssistantProjectionId,
  runtimeOf,
} from "./runtime";
import { useConversationStore } from "./store";
import { isWritingTurnPhase } from "./turnPhase";
import type { ConversationRuntime, MemoryUpdate, Message } from "./types";

/** Stable empty process — a fresh `[]` per call would re-render every subscriber. */
const NO_PROCESS: ProcessStep[] = [];

/** Stable empty window — a fresh `[]` per closed-find/outline tick would re-render. */
export const NO_ACTIVE_MESSAGES: Message[] = [];

function usePaneSliceKey(): string {
  const scoped = useChatPaneScope();
  const current = useConversationStore((s) =>
    scoped === undefined ? s.currentConversationId : null,
  );
  return (scoped === undefined ? current : scoped) ?? DRAFT_KEY;
}

function usePaneRuntime<T>(project: (rt: ConversationRuntime) => T): T {
  const scoped = useChatPaneScope();
  return useConversationStore((s) => {
    const key =
      scoped === undefined
        ? (s.currentConversationId ?? DRAFT_KEY)
        : (scoped ?? DRAFT_KEY);
    return project(runtimeOf(s, key));
  });
}

export const useActiveMessages = (): Message[] =>
  usePaneRuntime((rt) => rt.messages);

/** Whether the loaded window has any bubbles — stable during a streaming tick. */
export const useActiveHasMessages = (): boolean =>
  usePaneRuntime((rt) => rt.messages.length > 0);

export const useActiveFirstMessageId = (): string | null =>
  usePaneRuntime((rt) => rt.messages[0]?.id ?? null);

/**
 * Stick-to-bottom key for the live tail.
 * Isolated so ChatView chrome does not have to subscribe to the whole `messages[]`.
 *
 * `isStreaming` is part of the key so a settle (live → 收场过程折, height
 * drops with no content/reasoning length change) still hits the layout-effect
 * pin in {@link useChatScroll} instead of waiting for a later ResizeObserver.
 */
export function stickContentKey(
  last:
    | (Pick<Message, "id" | "content" | "isStreaming"> & {
        reasoning?: string | null;
      })
    | null
    | undefined,
): string {
  if (!last) return "";
  return `${last.id}-${last.content.length}-${last.reasoning?.length ?? 0}-${last.isStreaming ? "1" : "0"}`;
}

export const useActiveStickContentKey = (): string =>
  usePaneRuntime((rt) => stickContentKey(rt.messages.at(-1)));

export const useActiveUserTurnCount = (): number =>
  usePaneRuntime((rt) => {
    let n = 0;
    for (const m of rt.messages) {
      if (m.role === "user") n++;
    }
    return n;
  });

export const useActiveLastAssistantProjectionId = (): string | null =>
  usePaneRuntime((rt) => lastAssistantProjectionId(rt.messages));

export const usePrecedingUserMessageId = (
  assistantMessageId: string | null,
): string | null => {
  const key = usePaneSliceKey();
  return useConversationStore((s) => {
    if (!assistantMessageId) return null;
    return precedingUserMessageId(
      runtimeOf(s, key).messages,
      assistantMessageId,
    );
  });
};

export const useActiveMessageHasVisibleText = (
  messageId: string | null,
): boolean => {
  const key = usePaneSliceKey();
  return useConversationStore((s) => {
    if (!messageId) return false;
    const m = runtimeOf(s, key).messages.find((x) => x.id === messageId);
    return m ? Boolean(visibleMessageText(m)) : false;
  });
};

/**
 * The `content` of one message in the active conversation by id (or "" when absent /
 * id is null). A narrow slice — subscribing to just this string means a consumer (the
 * SidePanel content tab) re-renders only when THAT message's text changes, not on every
 * streaming tick that mints a new `messages` array (白屏卡死修复·Stage 3 收窄订阅).
 */
export const useActiveMessageContent = (messageId: string | null): string => {
  const key = usePaneSliceKey();
  return useConversationStore((s) =>
    messageId
      ? (runtimeOf(s, key).messages.find((m) => m.id === messageId)?.content ??
        "")
      : "",
  );
};

/**
 * 某条消息的过程线（本会话内按 client / server id 任一命中）。缺失 → 稳定空数组。
 * 供时间线痕迹行读「这一回合 CEO 自己调了什么」，不必订阅整个 messages。
 */
export const useActiveMessageProcess = (
  messageId: string | null,
): ProcessStep[] => {
  const key = usePaneSliceKey();
  return useConversationStore((s) => {
    if (!messageId) return NO_PROCESS;
    const found = runtimeOf(s, key).messages.find(
      (m) => m.id === messageId || m.serverMessageId === messageId,
    );
    return found?.process ?? NO_PROCESS;
  });
};

export const useActiveMemoryUpdates = (): MemoryUpdate[] =>
  usePaneRuntime((rt) => rt.memoryUpdates);

export const useActiveGenerating = (): boolean =>
  usePaneRuntime((rt) => conversationStillWriting(rt));

/** 桌面：最近一回合执行路径（`sidecar` / null）。 */
export const useActiveExecutionVia = (): ConversationRuntime["executionVia"] =>
  usePaneRuntime((rt) => rt.executionVia);

export const useActiveTurnPhase = () => usePaneRuntime((rt) => rt.turnPhase);

/**
 * 列表蓝点 / 输入区停止键：本轮还在写。队员齐了把图标成完成之后，
 * ``isGenerating`` 仍可能被切走误清；回合 phase 要等这句话真正停笔。
 * 不看协作图活体——图可以显示人齐了，聊天铬条仍跟这一轮走。
 */
export function conversationStillWriting(rt: ConversationRuntime): boolean {
  return rt.isGenerating || isWritingTurnPhase(rt.turnPhase);
}

/**
 * Last assistant bubble while this conversation is still writing.
 * Empty-shell hide must not unmount that placeholder (hydrate may clear
 * ``isStreaming`` before the engine is on the wire).
 */
export function liveTailWritingForMessage(
  rt: ConversationRuntime,
  messageId: string,
): boolean {
  if (!conversationStillWriting(rt)) return false;
  return lastAssistantMessageId(rt.messages) === messageId;
}

export const useLiveTailWriting = (messageId: string): boolean => {
  const key = usePaneSliceKey();
  return useConversationStore((s) =>
    liveTailWritingForMessage(runtimeOf(s, key), messageId),
  );
};

export const useConversationGenerating = (conversationId: string): boolean =>
  useConversationStore((s) =>
    conversationStillWriting(runtimeOf(s, conversationId)),
  );

export const useActiveError = (): string | null =>
  usePaneRuntime((rt) => rt.error);

export const useActiveRetry = (): (() => void) | null =>
  usePaneRuntime((rt) => rt.retry);

export const useActiveErrorAction = (): ErrorAction | null =>
  usePaneRuntime((rt) => rt.errorAction);

export const useActiveMessageFocus = (): { id: string; nonce: number } | null =>
  usePaneRuntime((rt) => rt.messageFocus);

export const useActiveHasMoreBefore = (): boolean =>
  usePaneRuntime((rt) => rt.hasMoreBefore);

export const useActiveHasMoreAfter = (): boolean =>
  usePaneRuntime((rt) => rt.hasMoreAfter);

export const useActiveLoadingOlder = (): boolean =>
  usePaneRuntime((rt) => rt.loadingOlder);

export const useActiveLoadingNewer = (): boolean =>
  usePaneRuntime((rt) => rt.loadingNewer);

export const getActiveRuntime = (): ConversationRuntime =>
  activeRuntime(useConversationStore.getState());

export const getRuntime = (
  conversationId?: string | null,
): ConversationRuntime =>
  runtimeOf(useConversationStore.getState(), conversationId);
