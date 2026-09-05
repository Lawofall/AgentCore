import { useLocation } from "react-router-dom";

/**
 * Conversation shown as the main canvas. Route is the location authority;
 * `currentConversationId` is the runtime pointer (persists across sections,
 * pins the sidebar list) and must not drive selected chrome.
 *
 * `/conversations` (manage) is not a conversation location.
 */
export function conversationLocationId(pathname: string): string | null {
  const match = /^\/conversations\/([^/]+)/.exec(pathname);
  return match?.[1] ?? null;
}

export function isConversationLocation(
  pathname: string,
  conversationId: string,
): boolean {
  return conversationLocationId(pathname) === conversationId;
}

export function useConversationLocationId(): string | null {
  return conversationLocationId(useLocation().pathname);
}
