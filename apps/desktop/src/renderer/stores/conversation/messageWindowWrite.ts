/**
 * Whole-window write contract (step 1): residency + strict-richer dominance.
 *
 * Do not use message `updatedAt` (REST has none) or treat uuid message ids as
 * monotonic order. Compare by id/serverMessageId identity + observable richness.
 */
import { DRAFT_KEY } from "./runtime";
import type { Message } from "./types";

export type MessageWindowWriteRejectReason =
  | "reject_not_resident"
  | "reject_not_richer"
  | "reject_generating"
  | "reject_active_has_more_after";

/** Identity keys for matching live client bubbles to REST rows. */
export function messageIdentityKeys(m: Message): string[] {
  const keys = [m.id];
  if (m.serverMessageId && m.serverMessageId !== m.id) {
    keys.push(m.serverMessageId);
  }
  return keys;
}

/**
 * Observable richness of one message (lengths / counts only — no timestamps).
 * Higher = more complete (content, journal, process, attachments, …).
 */
export function messageRichnessScore(m: Message): number {
  return (
    (m.content?.length ?? 0) +
    (m.reasoning?.length ?? 0) +
    (m.runs?.events?.length ?? 0) +
    (m.process?.length ?? 0) +
    (m.attachments?.length ?? 0) +
    (m.evidenceLedger?.length ?? 0) +
    (m.captainContext?.length ?? 0)
  );
}

function findMatchingMessage(
  haystack: Message[],
  needle: Message,
): Message | undefined {
  const keys = new Set(messageIdentityKeys(needle));
  return haystack.find((m) => messageIdentityKeys(m).some((k) => keys.has(k)));
}

/**
 * Incoming window strictly dominates existing: every existing message is present
 * with ≥ richness, and either the set grows or at least one message is richer.
 * Equal / thinner / same-tail-thinner (e.g. harvest incomplete) → false.
 */
export function isMessageWindowStrictlyRicher(
  incoming: Message[],
  existing: Message[],
): boolean {
  if (existing.length === 0) return incoming.length > 0;

  let anyStrictlyRicher = false;
  for (const ex of existing) {
    const inc = findMatchingMessage(incoming, ex);
    if (!inc) return false;
    const exScore = messageRichnessScore(ex);
    const incScore = messageRichnessScore(inc);
    if (incScore < exScore) return false;
    if (incScore > exScore) anyStrictlyRicher = true;
  }

  if (incoming.length > existing.length) return true;
  return anyStrictlyRicher;
}

/**
 * Around-window write: keep the existing object when it is strictly thicker on
 * the same identity (search jump must not wipe an adopted live/cache bubble).
 * Historical slices with no overlap pass through unchanged.
 */
export function overlayIncomingWithRicherExisting(
  incoming: Message[],
  existing: Message[],
): Message[] {
  if (existing.length === 0) return incoming;
  return incoming.map((inc) => {
    const ex = findMatchingMessage(existing, inc);
    if (!ex) return inc;
    return messageRichnessScore(ex) > messageRichnessScore(inc) ? ex : inc;
  });
}

/**
 * Residency: non-active conversations missing from `byId` must not be
 * materialized by a whole-window write (LRU eviction must stick).
 * The currently open conversation may always receive a window write.
 */
export function isMessageWindowResident(
  currentConversationId: string | null,
  byId: Record<string, unknown>,
  targetConversationId: string | null | undefined,
): boolean {
  const key = targetConversationId ?? currentConversationId ?? DRAFT_KEY;
  const activeKey = currentConversationId ?? DRAFT_KEY;
  if (key === activeKey) return true;
  return key in byId;
}
