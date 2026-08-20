/**
 * Mobile cold-path Interaction store (`pausesTurn && !hot`).
 *
 * Live paint authority for ResumeCard — mirrors desktop InteractionStore cold
 * semantics (upsertRequired tombstone / new-host replace / stamp rekey+bind)
 * without importing desktop (cross-platform-frontend.mdc).
 */
import {
  INTERACTION_KIND_WIRE,
  USER_INTERACTION_KIND_VALUES,
  type UserInteractionKind,
} from "@agentcore/contract-types";
import { useSyncExternalStore } from "react";

/**
 * Durable resume kinds: gate that is not an in-process Future (`pausesTurn && !hot`).
 * The union is restated because generated flags are typed `boolean`, so TS cannot
 * prove the subset; runtime membership is derived. Lock: coldInteractions.test.ts.
 */
export type ColdResumeKind = Extract<
  UserInteractionKind,
  "ask_user" | "plan_review" | "team_preview"
>;

function isDurableCold(kind: UserInteractionKind): kind is ColdResumeKind {
  const wire = INTERACTION_KIND_WIRE[kind];
  return wire.pausesTurn && !wire.hot;
}

export const COLD_RESUME_KINDS =
  USER_INTERACTION_KIND_VALUES.filter(isDurableCold);

export type ColdInteractionStatus =
  | "pending"
  | "submitting"
  | "resolved"
  | "orphaned";

/** EPHEMERAL ``resume_deferred.busy_reason`` (冷 resume × live · deferred). */
export type ColdDeferredBusyReason = "wrap_up" | "live_turn";

export interface ColdInteractionEntry {
  id: string;
  kind: ColdResumeKind;
  status: ColdInteractionStatus;
  conversationId: string;
  /** Durable host message id once stamped; may be client turn id or empty pre-stamp. */
  messageId: string;
  payload: Record<string, unknown>;
  resolution?: Record<string, unknown>;
  /**
   * Set when the resume SSE emits ``resume_deferred`` — settlement is locked;
   * card paints「放行已记下…」while the same connection waits for the slot.
   */
  deferredBusyReason?: ColdDeferredBusyReason;
}

const COLD_RESUME_KIND_SET = new Set<string>(COLD_RESUME_KINDS);

export function isColdResumeKind(kind: string): kind is ColdResumeKind {
  return COLD_RESUME_KIND_SET.has(kind);
}

const REQUIRED_EVENT: Partial<Record<string, ColdResumeKind>> = {};
const RESOLVED_EVENT: Partial<Record<string, ColdResumeKind>> = {};
for (const kind of COLD_RESUME_KINDS) {
  const wire = INTERACTION_KIND_WIRE[kind];
  REQUIRED_EVENT[wire.requiredEvent] = kind;
  if (wire.resolvedEvent) RESOLVED_EVENT[wire.resolvedEvent] = kind;
}

export function kindFromColdRequiredEvent(
  eventType: string,
): ColdResumeKind | null {
  return REQUIRED_EVENT[eventType] ?? null;
}

export function kindFromColdResolvedEvent(
  eventType: string,
): ColdResumeKind | null {
  return RESOLVED_EVENT[eventType] ?? null;
}

export function idFromColdRequiredPayload(
  kind: ColdResumeKind,
  payload: Record<string, unknown>,
): string | null {
  const raw = payload[INTERACTION_KIND_WIRE[kind].idField];
  return typeof raw === "string" && raw.length > 0 ? raw : null;
}

function mapCopy(
  src: Map<string, ColdInteractionEntry>,
): Map<string, ColdInteractionEntry> {
  return new Map(src);
}

type Listener = () => void;

let byId = new Map<string, ColdInteractionEntry>();
const listeners = new Set<Listener>();

function emit(): void {
  for (const l of listeners) l();
}

function setById(next: Map<string, ColdInteractionEntry>): void {
  byId = next;
  emit();
}

export function getColdInteractionSnapshot(): Map<
  string,
  ColdInteractionEntry
> {
  return byId;
}

export function subscribeColdInteractions(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React hook — re-render when cold Interaction entries change. */
export function useColdInteractions(): Map<string, ColdInteractionEntry> {
  return useSyncExternalStore(
    subscribeColdInteractions,
    getColdInteractionSnapshot,
    getColdInteractionSnapshot,
  );
}

export function getColdInteraction(
  id: string,
): ColdInteractionEntry | undefined {
  return byId.get(id);
}

export function listColdPending(
  conversationId: string,
  kinds: readonly ColdResumeKind[] = COLD_RESUME_KINDS,
): ColdInteractionEntry[] {
  const want = new Set<string>(kinds);
  const out: ColdInteractionEntry[] = [];
  for (const entry of byId.values()) {
    if (entry.conversationId !== conversationId) continue;
    if (entry.status !== "pending" && entry.status !== "submitting") continue;
    if (!want.has(entry.kind)) continue;
    out.push(entry);
  }
  return out;
}

/**
 * Upsert from `*_required` SSE / recovery hydrate.
 * Tombstone rules (desktop parity):
 * - resolved stub (empty payload) → live required wins
 * - cold kind + new host messageId → replace settled for round-2+
 * - same host settled replay → keep tombstone
 */
export function upsertColdRequired(input: {
  kind: ColdResumeKind;
  conversationId: string;
  messageId: string;
  payload: Record<string, unknown>;
  status?: ColdInteractionStatus;
}): void {
  const id = idFromColdRequiredPayload(input.kind, input.payload);
  if (!id) return;
  const prev = byId.get(id);
  if (prev && (prev.status === "resolved" || prev.status === "orphaned")) {
    const forcedPending = input.status === "pending";
    const resolvedStub =
      prev.status === "resolved" &&
      (!prev.payload || Object.keys(prev.payload).length === 0);
    const coldNewHost =
      prev.status === "resolved" &&
      Boolean(input.messageId) &&
      Boolean(prev.messageId) &&
      input.messageId !== prev.messageId;
    if (!forcedPending && !resolvedStub && !coldNewHost) {
      return;
    }
  }
  if (prev && (prev.status === "pending" || prev.status === "submitting")) {
    let patched = prev;
    if (input.messageId && !prev.messageId) {
      patched = { ...patched, messageId: input.messageId };
    }
    if (patched !== prev) {
      const next = mapCopy(byId);
      next.set(id, patched);
      setById(next);
    }
    return;
  }
  const next = mapCopy(byId);
  next.set(id, {
    id,
    kind: input.kind,
    status: input.status ?? "pending",
    conversationId: input.conversationId,
    messageId: input.messageId || "",
    payload: input.payload,
  });
  setById(next);
}

/** Flip pending → submitting (cold resume click); no-op if not pending. */
export function markColdSubmitting(input: {
  kind: ColdResumeKind;
  id: string;
  resolution?: Record<string, unknown>;
}): boolean {
  const prev = byId.get(input.id);
  if (!prev || prev.status !== "pending") return false;
  const next = mapCopy(byId);
  next.set(input.id, {
    ...prev,
    status: "submitting",
    resolution: input.resolution ?? prev.resolution,
    deferredBusyReason: undefined,
  });
  setById(next);
  return true;
}

/**
 * EPHEMERAL ``resume_deferred`` — lock the card as「已记下」while the same
 * SSE waits for wrap_up / live_turn to free the slot (not a 409).
 */
export function markColdDeferred(input: {
  messageId: string;
  conversationId?: string;
  busyReason: ColdDeferredBusyReason;
}): void {
  if (!input.messageId) return;
  let changed = false;
  const next = mapCopy(byId);
  for (const [id, entry] of byId) {
    if (entry.messageId !== input.messageId) continue;
    if (
      input.conversationId &&
      entry.conversationId &&
      entry.conversationId !== input.conversationId
    ) {
      continue;
    }
    if (entry.status !== "pending" && entry.status !== "submitting") continue;
    next.set(id, {
      ...entry,
      status: "submitting",
      deferredBusyReason: input.busyReason,
    });
    changed = true;
  }
  if (changed) setById(next);
}

/** Resume stream refused / aborted before claim — restore an editable card. */
export function reopenColdPending(id: string): void {
  const prev = byId.get(id);
  if (!prev || prev.status !== "submitting") return;
  const next = mapCopy(byId);
  next.set(id, {
    ...prev,
    status: "pending",
    deferredBusyReason: undefined,
    resolution: undefined,
  });
  setById(next);
}

export function markColdResolved(input: {
  kind: ColdResumeKind;
  id: string;
  resolution?: Record<string, unknown>;
}): void {
  const prev = byId.get(input.id);
  const next = mapCopy(byId);
  if (prev) {
    next.set(input.id, {
      ...prev,
      status: "resolved",
      resolution: input.resolution ?? prev.resolution,
      deferredBusyReason: undefined,
    });
  } else {
    next.set(input.id, {
      id: input.id,
      kind: input.kind,
      status: "resolved",
      conversationId: "",
      messageId: "",
      payload: {},
      resolution: input.resolution,
    });
  }
  setById(next);
}

export function markColdOrphaned(
  id: string,
  opts?: {
    kind?: ColdResumeKind;
    conversationId?: string;
    messageId?: string;
  },
): void {
  const prev = byId.get(id);
  if (prev?.status === "resolved") return;
  const next = mapCopy(byId);
  if (prev) {
    next.set(id, { ...prev, status: "orphaned" });
    setById(next);
    return;
  }
  if (!opts?.kind) return;
  next.set(id, {
    id,
    kind: opts.kind,
    status: "orphaned",
    conversationId: opts.conversationId ?? "",
    messageId: opts.messageId ?? "",
    payload: {},
  });
  setById(next);
}

/** Re-key after message_start stamps the server id onto a client turn bubble. */
export function rekeyColdMessageId(
  fromMessageId: string,
  toMessageId: string,
): void {
  if (!fromMessageId || !toMessageId || fromMessageId === toMessageId) return;
  let changed = false;
  const next = mapCopy(byId);
  for (const [id, entry] of byId) {
    if (entry.messageId !== fromMessageId) continue;
    next.set(id, { ...entry, messageId: toMessageId });
    changed = true;
  }
  if (changed) setById(next);
}

/**
 * Bind unbound cold pending (empty messageId) to a newly stamped server id
 * so ResumeCard paints without waiting for recovery refresh.
 */
export function bindEmptyColdMessageId(
  conversationId: string,
  toMessageId: string,
): void {
  if (!conversationId || !toMessageId) return;
  let changed = false;
  const next = mapCopy(byId);
  for (const [id, entry] of byId) {
    if (entry.conversationId !== conversationId) continue;
    if (entry.messageId) continue;
    if (entry.status !== "pending" && entry.status !== "submitting") continue;
    next.set(id, { ...entry, messageId: toMessageId });
    changed = true;
  }
  if (changed) setById(next);
}

export function clearColdInteractions(conversationId?: string): void {
  if (conversationId === undefined) {
    setById(new Map());
    return;
  }
  const next = new Map<string, ColdInteractionEntry>();
  for (const [id, entry] of byId) {
    if (entry.conversationId !== conversationId) next.set(id, entry);
  }
  setById(next);
}

/** Apply a cold required / resolved / orphaned wire event. */
export function applyColdInteractionWireEvent(
  eventType: string,
  payload: Record<string, unknown>,
  conversationId: string,
  messageId: string,
): boolean {
  if (eventType === "interaction_orphaned") {
    const id =
      typeof payload.interaction_id === "string"
        ? payload.interaction_id
        : null;
    const kind =
      typeof payload.kind === "string" && isColdResumeKind(payload.kind)
        ? payload.kind
        : undefined;
    if (id && kind) {
      markColdOrphaned(id, { kind, conversationId, messageId });
      return true;
    }
    if (id && !kind) {
      // Non-cold orphan — ignore (hot kinds stay on fold / PauseCard).
      return false;
    }
    return false;
  }

  const requiredKind = kindFromColdRequiredEvent(eventType);
  if (requiredKind) {
    upsertColdRequired({
      kind: requiredKind,
      conversationId,
      messageId,
      payload,
    });
    return true;
  }

  const resolvedKind = kindFromColdResolvedEvent(eventType);
  if (resolvedKind) {
    const id = idFromColdRequiredPayload(resolvedKind, payload);
    if (id) {
      const prev = byId.get(id);
      // Settlement may prewrite ``*_resolved`` while the resume SSE is still
      // deferred (busy slot). Keep submitting so the card stays「已记下」until
      // claim+续跑 (message_start) or the stream settles.
      if (prev?.status === "submitting") {
        const next = mapCopy(byId);
        next.set(id, {
          ...prev,
          resolution: payload,
        });
        setById(next);
        return true;
      }
      markColdResolved({
        kind: resolvedKind,
        id,
        resolution: payload,
      });
    }
    return true;
  }

  return false;
}
