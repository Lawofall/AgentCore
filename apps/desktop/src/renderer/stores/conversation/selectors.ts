import { type ErrorAction, visibleMessageText } from "@/lib/errors";
import { precedingUserMessageId } from "@/lib/supportDiagnostics";
import type { ProcessStep } from "@/types/events";
import { activeRuntime, lastAssistantProjectionId, runtimeOf } from "./runtime";
import { useConversationStore } from "./store";
import type { ConversationRuntime, MemoryUpdate, Message } from "./types";

/** Stable empty process — a fresh `[]` per call would re-render every subscriber. */
const NO_PROCESS: ProcessStep[] = [];

/** Stable empty window — a fresh `[]` per closed-find/outline tick would re-render. */
export const NO_ACTIVE_MESSAGES: Message[] = [];

export const useActiveMessages = (): Message[] =>
  useConversationStore((s) => activeRuntime(s).messages);

/** Whether the loaded window has any bubbles — stable during a streaming tick. */
export const useActiveHasMessages = (): boolean =>
  useConversationStore((s) => activeRuntime(s).messages.length > 0);

export const useActiveFirstMessageId = (): string | null =>
  useConversationStore((s) => activeRuntime(s).messages[0]?.id ?? null);

/**
 * Stick-to-bottom key for the live tail (id + content/reasoning lengths).
 * Isolated so ChatView chrome does not have to subscribe to the whole `messages[]`.
 */
export const useActiveStickContentKey = (): string =>
  useConversationStore((s) => {
    const last = activeRuntime(s).messages.at(-1);
    return last
      ? `${last.id}-${last.content.length}-${last.reasoning?.length ?? 0}`
      : "";
  });

export const useActiveUserTurnCount = (): number =>
  useConversationStore((s) => {
    let n = 0;
    for (const m of activeRuntime(s).messages) {
      if (m.role === "user") n++;
    }
    return n;
  });

export const useActiveLastAssistantProjectionId = (): string | null =>
  useConversationStore((s) =>
    lastAssistantProjectionId(activeRuntime(s).messages),
  );

export const usePrecedingUserMessageId = (
  assistantMessageId: string | null,
): string | null =>
  useConversationStore((s) => {
    if (!assistantMessageId) return null;
    return precedingUserMessageId(
      activeRuntime(s).messages,
      assistantMessageId,
    );
  });

export const useActiveMessageHasVisibleText = (
  messageId: string | null,
): boolean =>
  useConversationStore((s) => {
    if (!messageId) return false;
    const m = activeRuntime(s).messages.find((x) => x.id === messageId);
    return m ? Boolean(visibleMessageText(m)) : false;
  });

/**
 * The `content` of one message in the active conversation by id (or "" when absent /
 * id is null). A narrow slice — subscribing to just this string means a consumer (the
 * SidePanel content tab) re-renders only when THAT message's text changes, not on every
 * streaming tick that mints a new `messages` array (白屏卡死修复·Stage 3 收窄订阅).
 */
export const useActiveMessageContent = (messageId: string | null): string =>
  useConversationStore((s) =>
    messageId
      ? (activeRuntime(s).messages.find((m) => m.id === messageId)?.content ??
        "")
      : "",
  );

/**
 * 某条消息的过程线（本会话内按 client / server id 任一命中）。缺失 → 稳定空数组。
 * 供时间线痕迹行读「这一回合 CEO 自己调了什么」，不必订阅整个 messages。
 */
export const useActiveMessageProcess = (
  messageId: string | null,
): ProcessStep[] =>
  useConversationStore((s) => {
    if (!messageId) return NO_PROCESS;
    const found = activeRuntime(s).messages.find(
      (m) => m.id === messageId || m.serverMessageId === messageId,
    );
    return found?.process ?? NO_PROCESS;
  });

export const useActiveMemoryUpdates = (): MemoryUpdate[] =>
  useConversationStore((s) => activeRuntime(s).memoryUpdates);

export const useActiveGenerating = (): boolean =>
  useConversationStore((s) => activeRuntime(s).isGenerating);

/** 桌面：最近一回合执行路径（`sidecar` / `cloud_bridge` / null）。 */
export const useActiveExecutionVia = (): ConversationRuntime["executionVia"] =>
  useConversationStore((s) => activeRuntime(s).executionVia);

export const useActiveTurnPhase = () =>
  useConversationStore((s) => activeRuntime(s).turnPhase);

export const useConversationGenerating = (conversationId: string): boolean =>
  useConversationStore((s) => runtimeOf(s, conversationId).isGenerating);

export const useActiveError = (): string | null =>
  useConversationStore((s) => activeRuntime(s).error);

export const useActiveRetry = (): (() => void) | null =>
  useConversationStore((s) => activeRuntime(s).retry);

export const useActiveErrorAction = (): ErrorAction | null =>
  useConversationStore((s) => activeRuntime(s).errorAction);

export const useActiveMessageFocus = (): { id: string; nonce: number } | null =>
  useConversationStore((s) => activeRuntime(s).messageFocus);

export const useActiveHasMoreBefore = (): boolean =>
  useConversationStore((s) => activeRuntime(s).hasMoreBefore);

export const useActiveHasMoreAfter = (): boolean =>
  useConversationStore((s) => activeRuntime(s).hasMoreAfter);

export const useActiveLoadingOlder = (): boolean =>
  useConversationStore((s) => activeRuntime(s).loadingOlder);

export const useActiveLoadingNewer = (): boolean =>
  useConversationStore((s) => activeRuntime(s).loadingNewer);

export const getActiveRuntime = (): ConversationRuntime =>
  activeRuntime(useConversationStore.getState());

export const getRuntime = (
  conversationId?: string | null,
): ConversationRuntime =>
  runtimeOf(useConversationStore.getState(), conversationId);
