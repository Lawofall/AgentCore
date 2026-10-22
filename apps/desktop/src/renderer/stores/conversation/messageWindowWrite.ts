/**
 * Message-window write helpers: identity, per-id richness (search-jump overlay
 * and unsynced outbox vs cloud), residency.
 *
 * Latest-window authority is the persisted server prefix. An unconfirmed local
 * tail (optimistic send / streaming assistant that still has no server id) stays
 * on this client until identity stamps catch up. Stream ownership is only the
 * dual-fold mutex — not a stand-in for that tail.
 */
import { stitchAssistantProcess } from "@/lib/processTimeline";
import { DRAFT_KEY } from "./runtime";
import type { Message } from "./types";

export type MessageWindowWriteRejectReason =
  | "reject_not_resident"
  | "reject_generating"
  | "reject_active_has_more_after"
  | "reject_empty_window";

export type UnconfirmedTailOpts = {
  /** Conversation still writing (optimistic send has flipped the lamp). */
  isGenerating?: boolean;
};

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
  haystack: readonly Message[],
  needle: Message,
): Message | undefined {
  const keys = new Set(messageIdentityKeys(needle));
  return haystack.find((m) => messageIdentityKeys(m).some((k) => keys.has(k)));
}

function lastUser(messages: readonly Message[]): Message | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i]?.role === "user") return messages[i];
  }
  return undefined;
}

function isEmptyAssistantPlaceholder(m: Message): boolean {
  return (
    !(m.content ?? "").trim() &&
    !(m.reasoning ?? "").trim() &&
    (m.process?.length ?? 0) === 0 &&
    !m.finishReason
  );
}

/**
 * Composer already painted this empty assistant behind the optimistic user.
 * `sendTurn` must reuse it — truncate + new id remounts the bubble (Thinking
 * flash + enter animation). Truncate remains for a failed-try leftover.
 */
export function isReusableSendAssistantPlaceholder(m: Message): boolean {
  if (m.role !== "assistant" || m.serverMessageId) return false;
  if (!isEmptyAssistantPlaceholder(m)) return false;
  if (m.error?.message?.trim() || m.runs?.error?.message?.trim()) return false;
  if (m.composingTool) return false;
  if ((m.runs?.events?.length ?? 0) > 0) return false;
  if (
    m.status === "incomplete" ||
    m.status === "failed" ||
    m.status === "complete"
  ) {
    return false;
  }
  return true;
}

/** The single clean assistant immediately after this send's user bubble. */
export function reusableSendAssistantId(
  messages: readonly Message[],
  optimisticUserId: string,
): string | undefined {
  const userIdx = messages.findIndex((m) => m.id === optimisticUserId);
  if (userIdx < 0) return undefined;
  const after = messages.slice(userIdx + 1);
  if (after.length !== 1) return undefined;
  const assistant = after[0];
  if (!assistant || !isReusableSendAssistantPlaceholder(assistant)) {
    return undefined;
  }
  return assistant.id;
}

/**
 * Optimistic Thinking (and the user bubble in front of it) this client still owns.
 *
 * Streaming without a server id is the live placeholder. `isGenerating`
 * plus an empty unfinished assistant is the same tail if streaming was
 * cleared (orphan settle on a previous leftover, not this send's bubble).
 */
export function unconfirmedLocalTail(
  messages: readonly Message[],
  opts: UnconfirmedTailOpts = {},
): Message[] {
  const last = messages.at(-1);
  if (!last || last.role !== "assistant" || last.serverMessageId) return [];
  const livePlaceholder =
    last.isStreaming ||
    (opts.isGenerating === true && isEmptyAssistantPlaceholder(last));
  if (!livePlaceholder) return [];
  const prev = messages.at(-2);
  if (prev?.role === "user" && !prev.serverMessageId) return [prev, last];
  return [last];
}

export function hasUnconfirmedLocalTail(
  messages: readonly Message[],
  opts: UnconfirmedTailOpts = {},
): boolean {
  return unconfirmedLocalTail(messages, opts).length > 0;
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

function journalIsSlim(m: Message): boolean {
  return m.runs?.eventsComplete === false;
}

function journalIsComplete(m: Message): boolean {
  return m.runs != null && m.runs.eventsComplete !== false;
}

/**
 * Server list is window authority (membership / order / body / status).
 * A slim ``eventsComplete=false`` row is not a newer journal — keep the
 * already-complete ``runs`` so offline opened cache can still fold the team
 * graph. Display ``process`` is re-folded from leftover ``content``.
 */
export function preserveCompleteRunsFromExisting(
  incoming: Message,
  existing: Message,
): Message {
  if (!journalIsSlim(incoming) || !journalIsComplete(existing)) {
    return incoming;
  }
  if (
    incoming.executionId &&
    existing.executionId &&
    incoming.executionId !== existing.executionId
  ) {
    return incoming;
  }
  // Keep the complete journal for the graph; re-fold leftover `content` into
  // process so a slim GET cannot be clobbered by a torn cache timeline.
  return stitchAssistantProcess({
    ...incoming,
    runs: existing.runs,
    process: existing.process ?? incoming.process,
    executionId: existing.executionId ?? incoming.executionId,
  });
}

/** Stamp complete journals from ``existing`` onto the server window by identity. */
export function overlayCompleteRunsOnServerWindow(
  incoming: Message[],
  existing: Message[],
): Message[] {
  if (existing.length === 0) return incoming;
  return incoming.map((inc) => {
    const ex = findMatchingMessage(existing, inc);
    return ex ? preserveCompleteRunsFromExisting(inc, ex) : inc;
  });
}

/** True when any row still needs GET …/messages/{id} before opened cache may store it. */
export function windowHasSlimJournal(messages: Message[]): boolean {
  return messages.some((m) => m.runs?.eventsComplete === false);
}

/**
 * Apply a persisted latest window over local memory.
 *
 * No unconfirmed tail → server list is the whole window (idle reopen / follow
 * catch-up of someone else's turn), with complete journals kept when the GET
 * slimmed ``runs``. Unconfirmed tail → overlay the persisted prefix, keep this
 * client's send (and stamp a same-content REST user onto the optimistic user
 * instead of duplicating it).
 */
export function adoptLatestWindowMessages(
  incoming: Message[],
  existing: Message[],
  opts: UnconfirmedTailOpts = {},
): Message[] {
  const incomingKeptRuns = overlayCompleteRunsOnServerWindow(
    incoming,
    existing,
  );
  const suffix = unconfirmedLocalTail(existing, opts);
  if (suffix.length === 0) return incomingKeptRuns;

  const overlaid = overlayIncomingWithRicherExisting(
    incomingKeptRuns,
    existing,
  );
  const confirmed = existing.slice(0, existing.length - suffix.length);
  const result = [...overlaid];

  const incomingUserToStamp = (localUser: Message): Message | undefined => {
    const incUser = lastUser(incoming);
    if (!incUser) return undefined;
    if (findMatchingMessage(confirmed, incUser)) return undefined;
    if (incUser.content !== localUser.content) return undefined;
    return incUser;
  };

  for (const m of suffix) {
    if (findMatchingMessage(result, m)) continue;
    if (m.role === "user") {
      const incUser = incomingUserToStamp(m);
      if (incUser) {
        const idx = result.findIndex(
          (row) => findMatchingMessage([row], incUser) != null,
        );
        if (idx >= 0) {
          result[idx] = {
            ...m,
            serverMessageId: m.serverMessageId ?? incUser.id,
          };
        }
        continue;
      }
    }
    result.push(m);
  }
  return result;
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
