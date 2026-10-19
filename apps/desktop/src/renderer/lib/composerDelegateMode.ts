/**
 * Empty-draft 「统筹」until the first send creates a conversation.
 * Existing chats read ``Conversation.delegateMode``.
 * New chats / empty draft follow last-used (`chat:delegate:last`); never picked → off.
 */

import { uiGet, uiRemove, uiSet } from "@/lib/uiStorage";

const LAST_USED_LEAF = "chat:delegate:last";

const listeners = new Set<() => void>();
/** Null = follow last-used. Explicit boolean is this empty-draft session. */
let draft: boolean | null = null;

function emit(): void {
  for (const listener of listeners) listener();
}

function readLastUsed(): boolean {
  return uiGet<boolean>(LAST_USED_LEAF) === true;
}

export function rememberDelegateSelection(on: boolean): void {
  uiSet(LAST_USED_LEAF, Boolean(on));
}

export function getComposerDraftDelegateMode(): boolean {
  return draft ?? readLastUsed();
}

export function setComposerDraftDelegateMode(on: boolean): void {
  const next = Boolean(on);
  rememberDelegateSelection(next);
  if (draft === next) return;
  draft = next;
  emit();
}

/** After the first send creates a chat: drop the overlay, keep last-used. */
export function clearComposerDraftDelegateMode(): void {
  if (draft == null) return;
  draft = null;
  emit();
}

export function subscribeComposerDraftDelegateMode(
  listener: () => void,
): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** @internal vitest */
export function __resetComposerDelegateModeForTests(): void {
  draft = null;
  uiRemove(LAST_USED_LEAF);
  emit();
}
