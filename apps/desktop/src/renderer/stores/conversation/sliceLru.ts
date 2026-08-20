/**
 * In-memory conversation-slice LRU for warm reopen after switch.
 * Busy slices (generating / pending interaction) are never evicted.
 */
import { useInteractionStore } from "@/stores/interactions";
import { DRAFT_KEY } from "./runtime";
import type { ConversationRuntime } from "./types";

/** Max idle non-active conversation slices retained after switch. */
export const CONVERSATION_SLICE_LRU_LIMIT = 5;

export function isConversationSliceBusy(
  key: string,
  slice: ConversationRuntime | undefined,
): boolean {
  if (!slice) return false;
  if (slice.isGenerating) return true;
  const pending = useInteractionStore.getState().listPending(key);
  return pending.length > 0;
}

/** Move `key` to most-recent; drafts are not tracked. */
export function touchConversationSliceLru(
  order: string[],
  key: string,
): string[] {
  if (key === DRAFT_KEY) return order;
  const next = order.filter((k) => k !== key);
  next.push(key);
  return next;
}

/**
 * Drop idle draft when leaving it; keep up to {@link CONVERSATION_SLICE_LRU_LIMIT}
 * idle non-active slices that have messages (MRU); never evict busy or the active key.
 * Empty idle slices are dropped immediately (no warm-reopen value).
 */
export function pruneConversationSlices(
  byId: Record<string, ConversationRuntime>,
  order: string[],
  activeKey: string,
  prevKey: string,
): { byId: Record<string, ConversationRuntime>; sliceLruOrder: string[] } {
  const nextById = { ...byId };
  let nextOrder = touchConversationSliceLru(order, activeKey);

  // Idle draft is not a warm-reopen target — drop when leaving.
  if (
    prevKey === DRAFT_KEY &&
    prevKey !== activeKey &&
    nextById[DRAFT_KEY] &&
    !isConversationSliceBusy(DRAFT_KEY, nextById[DRAFT_KEY])
  ) {
    delete nextById[DRAFT_KEY];
  }

  const protectedKeys = new Set<string>([activeKey]);
  for (const [key, slice] of Object.entries(nextById)) {
    if (key === DRAFT_KEY) continue;
    if (isConversationSliceBusy(key, slice)) protectedKeys.add(key);
  }

  // Drop empty idle non-active slices (cannot warm-reopen).
  for (const key of Object.keys(nextById)) {
    if (key === DRAFT_KEY || protectedKeys.has(key)) continue;
    const slice = nextById[key];
    if (!slice || slice.messages.length === 0) {
      delete nextById[key];
    }
  }

  // Idle non-active candidates with messages, oldest first.
  const idle: string[] = [];
  const seen = new Set<string>();
  for (const key of nextOrder) {
    if (!nextById[key] || protectedKeys.has(key) || key === DRAFT_KEY) continue;
    idle.push(key);
    seen.add(key);
  }
  for (const key of Object.keys(nextById)) {
    if (key === DRAFT_KEY || protectedKeys.has(key) || seen.has(key)) continue;
    idle.unshift(key);
  }

  const overflow = idle.length - CONVERSATION_SLICE_LRU_LIMIT;
  if (overflow > 0) {
    for (const key of idle.slice(0, overflow)) {
      delete nextById[key];
    }
  }

  nextOrder = nextOrder.filter((key) => key in nextById && key !== DRAFT_KEY);
  return { byId: nextById, sliceLruOrder: nextOrder };
}
