/**
 * Live cold ResumeCard paint selector (mobile).
 *
 * Authority = cold Interaction pending/submitting (stamp-bound); recovery
 * `paused[]` is the reopen shell for entries not yet covered by IX. A terminal
 * IX (`resolved` / `orphaned`) suppresses that shell — follow `*_resolved`
 * must not leave a clickable card. Desktop semantic parity without importing
 * desktop code.
 */
import type { PausedTurnSummary } from "@/api/turn";
import {
  type ColdDeferredBusyReason,
  type ColdInteractionEntry,
  type ColdInteractionStatus,
  type ColdResumeKind,
  isColdResumeKind,
} from "@/lib/coldInteractions";
import { pickKickoffRevisionFields } from "@/lib/kickoffRevision";

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

/** Assistant host bubble used to resolve durable resume keys. */
export interface ColdResumeHost {
  role: "assistant";
  /** Local turn / bubble id (client UUID before stamp). */
  id: string;
  /** Server message_id once message_start stamped it. */
  serverMessageId?: string | null;
}

/** ResumeCard paint DTO — recovery shell + live IX status / deferred wait. */
export type VisibleColdResume = PausedTurnSummary & {
  interactionStatus?: ColdInteractionStatus;
  deferredBusyReason?: ColdDeferredBusyReason;
};

/**
 * Resolve the stamped server resume key for a cold Interaction entry.
 * No stamp yet ⇒ null (do not paint a clickable card on the client UUID).
 * Empty entry.messageId ⇒ latest stamped assistant (late stamp bind path).
 */
export function resolveColdResumeKeyFromHosts(
  hosts: ColdResumeHost[],
  entryMessageId: string,
): string | null {
  if (!entryMessageId) {
    for (let i = hosts.length - 1; i >= 0; i--) {
      const sid = hosts[i]?.serverMessageId?.trim();
      if (sid) return sid;
    }
    return null;
  }
  const host = hosts.find(
    (m) =>
      m.id === entryMessageId ||
      (m.serverMessageId != null && m.serverMessageId === entryMessageId),
  );
  if (host) {
    const sid = host.serverMessageId?.trim();
    return sid || null;
  }
  // Recovery / journal hydrate keys by durable server id directly.
  return entryMessageId;
}

/**
 * Durable host key for cold `*_required` upsert (投影键不断档).
 *
 * Never pin pending onto an unsealed client bubble when a stamp exists:
 * prefer `resumeStamp` → preferred host's stamp → latest stamped host.
 * Unsealed preferred with no stamp available → `""` (bindEmpty on later
 * message_start) — keep the no-stamp / no-clickable-card latch.
 */
export function resolveColdBindHostId(
  hosts: ColdResumeHost[],
  preferredHostId: string,
  opts?: { resumeStamp?: string | null },
): string {
  const resumeStamp = opts?.resumeStamp?.trim();
  if (resumeStamp) return resumeStamp;

  const preferred = preferredHostId.trim();
  if (preferred) {
    const hit = hosts.find(
      (m) =>
        m.id === preferred ||
        (m.serverMessageId != null && m.serverMessageId === preferred),
    );
    if (hit) {
      const sid = hit.serverMessageId?.trim();
      if (sid) return sid;
      // Matched an unsealed bubble — do not nail; fall through.
    } else {
      // Not in the live host list: treat as durable resume / journal key
      // (same-turn continue before the host row catches up).
      return preferred;
    }
  }

  for (let i = hosts.length - 1; i >= 0; i--) {
    const sid = hosts[i]?.serverMessageId?.trim();
    if (sid) return sid;
  }
  return "";
}

/** Build ResumeCard DTO from a cold Interaction `*_required` payload. */
export function entryToPausedSummary(
  entry: ColdInteractionEntry,
  resumeMessageId: string,
  opts?: { userMessage?: string; userMessageId?: string },
): PausedTurnSummary | null {
  if (!isColdResumeKind(entry.kind)) return null;
  const p = entry.payload;
  const kind: ColdResumeKind = entry.kind;
  const base: PausedTurnSummary = {
    message_id: resumeMessageId,
    checkpoint_id: entry.id,
    kind,
    user_message: opts?.userMessage ?? "",
    user_message_id: opts?.userMessageId ?? "",
    question: str(p.question),
    form: str(p.form),
    headline: str(p.headline),
    motion: str(p.motion),
    primitive: str(p.primitive, "delegate") || "delegate",
    max_rounds: Number(p.max_rounds ?? 0) || 0,
    thorough: p.thorough !== false,
    browser_login: p.browser_login === true,
  };

  if (kind === "ask_user") {
    return {
      ...base,
      assumptions: arr(p.assumptions) as PausedTurnSummary["assumptions"],
      questions: arr(p.questions) as PausedTurnSummary["questions"],
      ...(typeof p.intent === "string" || p.intent == null
        ? {
            intent: (p.intent ?? null) as PausedTurnSummary["intent"],
          }
        : {}),
    };
  }

  if (kind === "plan_review") {
    return {
      ...base,
      steps: arr(p.steps) as PausedTurnSummary["steps"],
      pending: arr(p.pending) as PausedTurnSummary["pending"],
    };
  }

  // team_preview
  return {
    ...base,
    ...pickKickoffRevisionFields(p),
    workers: arr(p.workers) as PausedTurnSummary["workers"],
    tools: arr(p.tools).filter((t): t is string => typeof t === "string"),
    sides: arr(p.sides) as PausedTurnSummary["sides"],
    // REST / journal 可选 moderator_*；schema 未列时宽松透传（absent → 不写）。
    ...(() => {
      const extra: Record<string, unknown> = {};
      if (typeof p.moderator_run_id === "string" && p.moderator_run_id.trim()) {
        extra.moderator_run_id = p.moderator_run_id.trim();
      }
      if (typeof p.moderator_model === "string" && p.moderator_model.trim()) {
        extra.moderator_model = p.moderator_model.trim();
      }
      if (p.moderator_origin === "platform" || p.moderator_origin === "byok") {
        extra.moderator_origin = p.moderator_origin;
      }
      if (
        typeof p.moderator_provider_id === "string" &&
        p.moderator_provider_id
      ) {
        extra.moderator_provider_id = p.moderator_provider_id;
      }
      return extra;
    })(),
  } as PausedTurnSummary;
}

/** Reconstruct a minimal `*_required` payload from a recovery paused frame. */
export function pausedSummaryToRequiredPayload(
  paused: PausedTurnSummary,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    checkpoint_id: paused.checkpoint_id,
    question: paused.question ?? "",
    form: paused.form ?? "",
    headline: paused.headline ?? "",
    motion: paused.motion ?? "",
    primitive: paused.primitive ?? "delegate",
    max_rounds: paused.max_rounds ?? 0,
    thorough: paused.thorough !== false,
    browser_login: paused.browser_login === true,
  };
  if (paused.assumptions) payload.assumptions = paused.assumptions;
  if (paused.questions) payload.questions = paused.questions;
  if (paused.intent != null) payload.intent = paused.intent;
  if (paused.steps) payload.steps = paused.steps;
  if (paused.pending) payload.pending = paused.pending;
  if (paused.workers) payload.workers = paused.workers;
  if (paused.tools) payload.tools = paused.tools;
  if (paused.sides) payload.sides = paused.sides;
  const lineage = pickKickoffRevisionFields(
    paused as unknown as Record<string, unknown>,
  );
  if (lineage.revision != null) payload.revision = lineage.revision;
  if (lineage.revised_from) payload.revised_from = lineage.revised_from;
  if (lineage.revision_note) payload.revision_note = lineage.revision_note;
  const loose = paused as PausedTurnSummary & Record<string, unknown>;
  if (typeof loose.moderator_run_id === "string" && loose.moderator_run_id) {
    payload.moderator_run_id = loose.moderator_run_id;
  }
  if (typeof loose.moderator_model === "string" && loose.moderator_model) {
    payload.moderator_model = loose.moderator_model;
  }
  if (
    loose.moderator_origin === "platform" ||
    loose.moderator_origin === "byok"
  ) {
    payload.moderator_origin = loose.moderator_origin;
  }
  if (
    typeof loose.moderator_provider_id === "string" &&
    loose.moderator_provider_id
  ) {
    payload.moderator_provider_id = loose.moderator_provider_id;
  }
  return payload;
}

/**
 * One pending kickoff card per conversation. Mobile has no `surfacedSeq`;
 * equivalent order = later stamped host, then live IX over paused shell,
 * then encounter order. ask_user / plan_review are not collapsed.
 */
function keepLatestTeamPreview(
  cards: VisibleColdResume[],
  hosts: ColdResumeHost[],
  ixIds: Set<string>,
): VisibleColdResume[] {
  let best = -1;
  let bestHost = Number.NEGATIVE_INFINITY;
  let bestIx = false;
  let bestI = -1;
  for (let i = 0; i < cards.length; i++) {
    const card = cards[i];
    if (card.kind !== "team_preview") continue;
    const host = hosts.findIndex(
      (h) =>
        h.id === card.message_id ||
        (h.serverMessageId != null && h.serverMessageId === card.message_id),
    );
    const ix = ixIds.has(card.checkpoint_id);
    const later =
      best < 0 ||
      host > bestHost ||
      (host === bestHost && ix && !bestIx) ||
      (host === bestHost && ix === bestIx && i > bestI);
    if (later) {
      best = i;
      bestHost = host;
      bestIx = ix;
      bestI = i;
    }
  }
  if (best < 0) return cards;
  return cards.filter((c, i) => c.kind !== "team_preview" || i === best);
}

/**
 * Pure paint selector: cold Interaction pending is live authority;
 * recovery `paused` covers reopen shells not already covered by IX.
 */
export function selectVisibleColdResumes(args: {
  conversationId: string;
  byId: Map<string, ColdInteractionEntry>;
  paused: PausedTurnSummary[];
  hosts: ColdResumeHost[];
  userMessage?: string;
  userMessageId?: string;
}): VisibleColdResume[] {
  const { conversationId, byId, paused, hosts } = args;
  const covered = new Set<string>();
  const out: VisibleColdResume[] = [];

  for (const entry of byId.values()) {
    if (entry.conversationId !== conversationId) continue;
    if (!isColdResumeKind(entry.kind)) continue;
    if (!entry.id || !entry.payload) continue;
    const actionable =
      entry.status === "pending" || entry.status === "submitting";
    if (!actionable) continue;
    const resumeKey = resolveColdResumeKeyFromHosts(hosts, entry.messageId);
    if (!resumeKey) continue;
    const turn = entryToPausedSummary(entry, resumeKey, {
      userMessage: args.userMessage,
      userMessageId: args.userMessageId,
    });
    if (!turn) continue;
    out.push({
      ...turn,
      interactionStatus: entry.status,
      deferredBusyReason: entry.deferredBusyReason,
    });
    covered.add(entry.id);
  }

  for (const p of paused) {
    if (covered.has(p.checkpoint_id)) continue;
    const ixStatus = byId.get(p.checkpoint_id)?.status;
    // Terminal IX: card is done (this device or another). Do not keep a
    // clickable recovery shell — desktop `removePausedTurn` on `*_resolved`.
    if (ixStatus === "orphaned" || ixStatus === "resolved") continue;
    out.push(p);
  }

  return keepLatestTeamPreview(out, hosts, covered);
}
