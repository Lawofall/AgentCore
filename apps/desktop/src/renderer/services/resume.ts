import { hasLocalEngine } from "@/lib/capabilities";
import { api } from "@/services/api";
import { finalizeGeneratingForPausedConversation } from "@/services/turns/helpers";
import { getRuntime } from "@/stores/conversation";
import { clearInteractionPrompts } from "@/stores/interactionPrompts";
import {
  collectMessageJournalEvents,
  entryToCheckpoint,
  entryToColdResume,
  entryToPlanReview,
  entryToTeamPreview,
  isColdCheckpointSettled,
  settledColdIdsFromEvents,
  useInteractionStore,
} from "@/stores/interactions";
import type { InteractionEntry } from "@/stores/interactions";
import { registerColdJournalReader } from "@/stores/interactions/coldSettlement";
import {
  type PausedTurnEntry,
  type PendingResume,
  type ResumeOrigin,
  beginPausedSnapshot,
  usePausedTurnStore,
} from "@/stores/pausedTurns";
import type { components } from "@/types/api.generated";
import type {
  SidecarRunsPayload,
  SidecarUnsyncedTurnSummary,
} from "@shared/sidecar-contract";

type PausedTurnSummary = components["schemas"]["PausedTurnSummary"];
type TurnRecoveryResponse = components["schemas"]["TurnRecoveryResponse"];
type PendingInteractionSummary =
  components["schemas"]["PendingInteractionSummary"];

/**
 * Conversation recovery snapshot on reopen.
 * Desktop splits sidecar vs cloud facts; hydrate selects branch from facts,
 * never from `resolveSidecarRoot` (routing intent / React Query cache).
 */
export interface ConversationRecovery {
  sidecarLive: boolean;
  cloudLive: boolean;
  /**
   * True only after a successful cloud GET /recovery.
   * Failure leaves this false — `cloudLive=false` then means unknown, not confirmed idle.
   */
  cloudKnown: boolean;
  pausedCount: number;
  /** Sidecar-only: outbox ready / dead-open summaries for D5 projection. */
  unsynced: SidecarUnsyncedTurnSummary[];
  /**
   * Sidecar-only: pause-frame display runs (message_id → runs) for collab-graph
   * hydrate when cloud messages.runs is null (paused writeback skips journal).
   */
  pausedRuns?: Record<string, SidecarRunsPayload>;
  /** Sidecar-only: live turn key when `sidecarLive`. */
  turnId?: string;
}

/** Local hydrate path when main-process facts say so (D6 二次修订). */
export function shouldHydrateLocalRecovery(r: ConversationRecovery): boolean {
  return r.sidecarLive || r.unsynced.length > 0 || r.pausedCount > 0;
}

function hydratePendingInteractions(
  conversationId: string,
  items: PendingInteractionSummary[],
  opts: {
    since: number;
    confirmed: readonly ResumeOrigin[];
    sidecarLive?: boolean;
  },
  origin: ResumeOrigin = "server",
): void {
  useInteractionStore.getState().hydratePending(
    conversationId,
    items.map((i) => ({
      kind: i.kind,
      id: i.id,
      messageId: i.message_id,
      payload: i.payload ?? {},
      origin,
    })),
    opts,
  );
}

function checkpointIdsFromPaused(
  paused: Array<{ checkpoint_id?: string }>,
): Set<string> {
  const ids = new Set<string>();
  for (const p of paused) {
    if (typeof p.checkpoint_id === "string" && p.checkpoint_id) {
      ids.add(p.checkpoint_id);
    }
  }
  return ids;
}

function asPendingInteractions(
  res: TurnRecoveryResponse,
): PendingInteractionSummary[] {
  const items = res.pending_interactions ?? [];
  return items.filter(
    (i): i is PendingInteractionSummary =>
      !!i &&
      typeof i.id === "string" &&
      typeof i.kind === "string" &&
      typeof i.message_id === "string",
  );
}

/**
 * Merge paused frames by message_id (sidecar wins on collision), tagging each
 * frame with its durable origin so resume routing stays correct for mixed
 * cloud+sidecar sessions (never a single conversation-wide origin).
 */
function mergePausedWithOrigin(
  sidecar: PausedTurnSummary[],
  cloud: PausedTurnSummary[],
): PausedTurnEntry[] {
  const byId = new Map<string, PausedTurnEntry>();
  for (const p of cloud) {
    if (p?.message_id) byId.set(p.message_id, { summary: p, origin: "server" });
  }
  for (const p of sidecar) {
    if (p?.message_id)
      byId.set(p.message_id, { summary: p, origin: "sidecar" });
  }
  return [...byId.values()];
}

async function loadCloudRecovery(conversationId: string): Promise<{
  cloudLive: boolean;
  paused: PausedTurnSummary[];
  pending: PendingInteractionSummary[];
}> {
  const res = await api.get<TurnRecoveryResponse>(
    `/v1/conversations/${conversationId}/recovery`,
  );
  return {
    cloudLive: Boolean(res.live_running),
    paused: (res.paused ?? []) as PausedTurnSummary[],
    pending: asPendingInteractions(res),
  };
}

/**
 * Load a conversation's recovery state into the store on reopen (best-effort).
 *
 * Desktop (`hasLocalEngine`): unconditionally query local recovery IPC **and**
 * cloud GET /recovery in parallel; failures do not drag each other.
 * Web: cloud-only (unchanged).
 */
export async function loadRecovery(
  conversationId: string,
): Promise<ConversationRecovery> {
  // 观察起点要在发请求**之前**取：这之后才浮现的挂起卡，本次快照读不到，回空也不能清。
  const since = beginPausedSnapshot();
  if (!hasLocalEngine()) {
    try {
      const cloud = await loadCloudRecovery(conversationId);
      usePausedTurnStore.getState().setForConversation(
        conversationId,
        cloud.paused.map((summary) => ({
          summary,
          origin: "server" as const,
        })),
        { since, confirmed: ["server"] },
      );
      hydratePendingInteractions(conversationId, cloud.pending, {
        since,
        confirmed: ["server"],
      });
      useInteractionStore
        .getState()
        .settleUnseenCold(
          conversationId,
          checkpointIdsFromPaused(cloud.paused),
          { since, confirmed: ["server"] },
        );
      if (cloud.paused.length > 0) {
        finalizeGeneratingForPausedConversation(conversationId);
      }
      return {
        sidecarLive: false,
        cloudLive: cloud.cloudLive,
        cloudKnown: true,
        pausedCount: cloud.paused.length,
        unsynced: [],
      };
    } catch {
      // Failure ≠ confirmed idle — leave stores untouched.
      return {
        sidecarLive: false,
        cloudLive: false,
        cloudKnown: false,
        pausedCount: 0,
        unsynced: [],
      };
    }
  }

  let sidecarLive = false;
  let sidecarKnown = false;
  let turnId: string | undefined;
  let unsynced: SidecarUnsyncedTurnSummary[] = [];
  let sidecarPaused: PausedTurnSummary[] = [];
  let pausedRuns: Record<string, SidecarRunsPayload> = {};
  let cloudLive = false;
  let cloudKnown = false;
  let cloudPaused: PausedTurnSummary[] = [];
  let cloudPending: PendingInteractionSummary[] | null = null;

  const localP = window.sidecarApi
    .recovery({ conversationId })
    .then((recovery) => {
      sidecarLive = recovery.liveRunning;
      sidecarKnown = true;
      turnId = recovery.turnId;
      unsynced = recovery.unsynced ?? [];
      sidecarPaused = (recovery.paused ?? []) as unknown as PausedTurnSummary[];
      pausedRuns = recovery.pausedRuns ?? {};
    })
    .catch(() => {
      /* local failure must not block cloud */
    });

  const cloudP = loadCloudRecovery(conversationId)
    .then((cloud) => {
      cloudLive = cloud.cloudLive;
      cloudKnown = true;
      cloudPaused = cloud.paused;
      cloudPending = cloud.pending;
    })
    .catch(() => {
      /* cloud failure must not block local; cloudKnown stays false */
    });

  await Promise.all([localP, cloudP]);

  const merged = mergePausedWithOrigin(sidecarPaused, cloudPaused);
  // 只有真被问到的那一路才有权清自己来源的壳：一路挂了 ≠ 它那边的帧没了。
  const confirmed: ResumeOrigin[] = [];
  if (sidecarKnown) confirmed.push("sidecar");
  if (cloudKnown) confirmed.push("server");
  // Cloud failure must not call hydratePending (unknown ≠ idle).
  if (cloudPending !== null) {
    hydratePendingInteractions(conversationId, cloudPending, {
      since,
      confirmed,
      sidecarLive,
    });
  }
  usePausedTurnStore
    .getState()
    .setForConversation(conversationId, merged, { since, confirmed });
  useInteractionStore
    .getState()
    .settleUnseenCold(
      conversationId,
      checkpointIdsFromPaused(merged.map((e) => e.summary)),
      { since, confirmed },
    );
  if (merged.length > 0) {
    finalizeGeneratingForPausedConversation(conversationId);
  }

  // Hot cards survive when a live turn will be attached (D6); only clear when
  // cloud is *known* idle and sidecar is idle — request failure must not orphan.
  if (!sidecarLive && cloudKnown && !cloudLive) {
    clearInteractionPrompts(conversationId);
  }

  return {
    sidecarLive,
    cloudLive,
    cloudKnown,
    pausedCount: merged.length,
    unsynced,
    ...(Object.keys(pausedRuns).length > 0 ? { pausedRuns } : {}),
    turnId,
  };
}

export function isClientOnlyResumeKey(
  conversationId: string,
  messageId: string,
): boolean {
  const assistant = getRuntime(conversationId).messages.find(
    (m) => m.role === "assistant" && m.id === messageId,
  );
  return assistant !== undefined && !assistant.serverMessageId;
}

/**
 * Resolve a resume POST key to the stamped server message id when possible.
 * If a pending card still hangs on the client bubble id, rekey it in place.
 */
export function resolveResumeMessageId(
  conversationId: string,
  messageId: string,
): string {
  const assistant = getRuntime(conversationId).messages.find(
    (m) =>
      m.role === "assistant" &&
      (m.id === messageId || m.serverMessageId === messageId),
  );
  const serverId = assistant?.serverMessageId;
  if (!serverId || serverId === messageId) return serverId ?? messageId;
  usePausedTurnStore.getState().rekeyMessageId(messageId, serverId);
  useInteractionStore.getState().rekeyMessageId(messageId, serverId);
  return serverId;
}

/**
 * Surface one durable resume card from InteractionStore pending cold kinds.
 *
 * Live operable authority is InteractionStore — ResumePrompt paints cold pending
 * directly. This helper remains a non-unique path for recovery/`toMessage` shell
 * (pausedTurns) + finalizeGenerating; live cards no longer require message_end.
 *
 * Resume key is the stamped `serverMessageId` only — without it, skip painting
 * so the UI never shows a clickable card that would 404 / trip the client-only
 * resume guard (aligns with pre-fallback live surface behavior).
 */
export function surfaceResumeFromAssistant(
  conversationId: string,
  assistant: { id: string; serverMessageId?: string },
  origin: ResumeOrigin,
  user?: { content?: string; id?: string },
): void {
  const resumeKey = assistant.serverMessageId;
  if (!resumeKey) return;

  const ix = useInteractionStore.getState();
  const pending = ix
    .listPending(conversationId, ["ask_user", "plan_review", "team_preview"])
    .filter(
      (e) =>
        !e.messageId ||
        e.messageId === assistant.id ||
        e.messageId === resumeKey,
    );

  // Prefer an explicit sidecar stamp (IX entry or existing paused shell) over a
  // caller default — `toMessage` historically hardcodes "server" and must not
  // clobber a live/recovery sidecar breakpoint for resume routing.
  const priorPausedOrigin = usePausedTurnStore
    .getState()
    .pending.find((p) => p.messageId === resumeKey)?.origin;
  const ixOrigin = pending.find((e) => e.origin)?.origin;
  const effectiveOrigin: ResumeOrigin =
    priorPausedOrigin === "sidecar" || ixOrigin === "sidecar"
      ? "sidecar"
      : (ixOrigin ?? origin);

  const base = {
    messageId: resumeKey,
    conversationId,
    userMessage: user?.content ?? "",
    userMessageId: user?.id ?? "",
    origin: effectiveOrigin,
  };

  let painted = false;
  const ask = pending.find((e) => e.kind === "ask_user");
  if (ask) {
    const cp = entryToCheckpoint(ask);
    usePausedTurnStore.getState().addLiveResume({
      ...base,
      checkpointId: cp.id,
      kind: "ask_user",
      steps: [],
      pending: [],
      workers: [],
      tools: [],
      primitive: "delegate",
      motion: "",
      form: "",
      sides: [],
      maxRounds: 0,
      thorough: true,
      question: cp.question,
      context: cp.context,
      assumptions: cp.assumptions,
      questions: cp.questions,
      intent: cp.intent,
      ...(cp.browserLogin ? { browserLogin: true as const } : {}),
    });
    painted = true;
  } else {
    const prEntry = pending.find((e) => e.kind === "plan_review");
    if (prEntry) {
      const pr = entryToPlanReview(prEntry);
      usePausedTurnStore.getState().addLiveResume({
        ...base,
        checkpointId: pr.id,
        kind: "plan_review",
        steps: pr.steps,
        pending: pr.pending,
        ceoReview: pr.ceoReview,
        workers: [],
        tools: [],
        primitive: "delegate",
        motion: "",
        form: "",
        sides: [],
        maxRounds: 0,
        thorough: true,
        question: "",
        context: "",
        assumptions: [],
        questions: [],
        intent: "decision",
      });
      painted = true;
    } else {
      const tpEntry = pending.find((e) => e.kind === "team_preview");
      if (tpEntry) {
        const tp = entryToTeamPreview(tpEntry);
        usePausedTurnStore.getState().addLiveResume({
          ...base,
          checkpointId: tp.id,
          kind: "team_preview",
          steps: [],
          pending: [],
          workers: tp.workers,
          tools: tp.tools ?? [],
          primitive: tp.primitive,
          ...(tp.headline ? { headline: tp.headline } : {}),
          ...(tp.revision != null ? { revision: tp.revision } : {}),
          ...(tp.revisedFrom ? { revisedFrom: tp.revisedFrom } : {}),
          ...(tp.revisionNote ? { revisionNote: tp.revisionNote } : {}),
          motion: tp.motion,
          form: tp.form,
          sides: tp.sides,
          maxRounds: tp.maxRounds,
          thorough: tp.thorough,
          ...(tp.moderatorModel ? { moderatorModel: tp.moderatorModel } : {}),
          ...(tp.moderatorOrigin
            ? { moderatorOrigin: tp.moderatorOrigin }
            : {}),
          ...(tp.moderatorProviderId
            ? { moderatorProviderId: tp.moderatorProviderId }
            : {}),
          ...(tp.sameModelDebate ? { sameModelDebate: true } : {}),
          question: "",
          context: "",
          assumptions: [],
          questions: [],
          // team_preview is the kickoff card — not a mid-turn decision ask.
          intent: "kickoff",
        });
        painted = true;
      }
    }
  }
  if (!painted) {
    // Stop = hard cancel: no Interaction ``*_required`` → no Resume card.
    return;
  }
  finalizeGeneratingForPausedConversation(conversationId);
}

/**
 * Resolve the stamped server resume key for a cold Interaction entry.
 *
 * Live paint contract: clickable ResumePrompt requires a durable server key
 * (never the bare client bubble id). When the matching assistant exists but
 * is not stamped yet, return null — ResumePrompt re-runs when
 * `setServerMessageIdOnLastMessage` stamps + rekeys/binds, and paints then.
 * Unbound entries (empty messageId) resolve to the latest stamped assistant
 * so a late stamp still completes the live card without hard refresh.
 */
export function resolveColdResumeKeyFromMessages(
  messages: Array<{
    role: string;
    id: string;
    serverMessageId?: string;
  }>,
  entryMessageId: string,
): string | null {
  if (!entryMessageId) {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && m.serverMessageId) return m.serverMessageId;
    }
    return null;
  }
  const assistant = messages.find(
    (m) =>
      m.role === "assistant" &&
      (m.id === entryMessageId || m.serverMessageId === entryMessageId),
  );
  if (assistant) {
    return assistant.serverMessageId ?? null;
  }
  // Journal / recovery hydrate keys entries by the durable server message id
  // (bubble id === server id); no live bubble may exist yet in partial hydrate.
  return entryMessageId;
}

export function resolveColdResumeKey(
  conversationId: string,
  entryMessageId: string,
): string | null {
  return resolveColdResumeKeyFromMessages(
    getRuntime(conversationId).messages,
    entryMessageId,
  );
}

/**
 * One pending kickoff card per conversation — latest by `surfacedSeq`
 * (missing seq = earliest). ask_user / plan_review are not collapsed.
 */
function keepLatestTeamPreview(cards: PendingResume[]): PendingResume[] {
  let best = -1;
  let bestSeq = Number.NEGATIVE_INFINITY;
  for (let i = 0; i < cards.length; i++) {
    if (cards[i].kind !== "team_preview") continue;
    const seq = cards[i].surfacedSeq ?? 0;
    if (best < 0 || seq > bestSeq) {
      best = i;
      bestSeq = seq;
    }
  }
  if (best < 0) return cards;
  return cards.filter((c, i) => c.kind !== "team_preview" || i === best);
}

registerColdJournalReader((conversationId) =>
  settledColdIdsFromEvents(
    collectMessageJournalEvents(getRuntime(conversationId).messages),
  ),
);

/**
 * Pure paint selector: InteractionStore cold pending is live authority;
 * pausedTurns covers recovery/`setForConversation` shells not covered by IX.
 * Clickability uses {@link isColdCheckpointSettled} — the only terminal gate.
 */
export function selectVisibleColdResumes(args: {
  conversationId: string;
  byId: Map<string, { status: string } & Partial<InteractionEntry>>;
  pausedPending: PendingResume[];
  messages: Array<{
    role: string;
    id: string;
    content?: string;
    serverMessageId?: string;
    runs?: { events?: ReadonlyArray<{ type?: string; payload?: unknown }> };
  }>;
}): PendingResume[] {
  const { conversationId, byId, pausedPending, messages } = args;
  const journalSettledIds = settledColdIdsFromEvents(
    collectMessageJournalEvents(messages),
  );
  const priorUser = [...messages].reverse().find((m) => m.role === "user");
  const pausedForConv = pausedPending.filter(
    (p) => p.conversationId === conversationId,
  );

  const covered = new Set<string>();
  const out: PendingResume[] = [];

  for (const entry of byId.values()) {
    if (entry.conversationId !== conversationId) continue;
    if (entry.status !== "pending" && entry.status !== "submitting") continue;
    if (
      entry.kind !== "ask_user" &&
      entry.kind !== "plan_review" &&
      entry.kind !== "team_preview"
    ) {
      continue;
    }
    if (!entry.id || !entry.payload) continue;
    const full = entry as InteractionEntry;
    if (
      isColdCheckpointSettled({
        checkpointId: full.id,
        entry: full,
        journalSettledIds,
      })
    ) {
      continue;
    }
    const resumeKey = resolveColdResumeKeyFromMessages(
      messages,
      full.messageId,
    );
    if (!resumeKey) continue;
    const origin: ResumeOrigin =
      full.origin ??
      pausedForConv.find((p) => p.checkpointId === full.id)?.origin ??
      "server";
    const turn = entryToColdResume(full, {
      resumeMessageId: resumeKey,
      userMessage: priorUser?.content ?? "",
      userMessageId: priorUser?.id ?? "",
      origin,
    });
    if (!turn) continue;
    out.push(
      typeof full.surfacedSeq === "number"
        ? { ...turn, surfacedSeq: full.surfacedSeq }
        : turn,
    );
    covered.add(full.id);
  }

  for (const p of pausedForConv) {
    if (covered.has(p.checkpointId)) continue;
    if (
      isColdCheckpointSettled({
        checkpointId: p.checkpointId,
        entry: byId.get(p.checkpointId),
        journalSettledIds,
      })
    ) {
      continue;
    }
    out.push(p);
  }

  return keepLatestTeamPreview(out);
}

/**
 * Cold resume cards for ResumePrompt (store snapshot convenience wrapper).
 */
export function listVisibleColdResumes(
  conversationId: string,
): PendingResume[] {
  return selectVisibleColdResumes({
    conversationId,
    byId: useInteractionStore.getState().byId,
    pausedPending: usePausedTurnStore.getState().pending,
    messages: getRuntime(conversationId).messages,
  });
}

/**
 * Prefer InteractionStore live origin; fall back to pausedTurns recovery shell.
 */
export function resolveResumeOrigin(
  conversationId: string,
  resumeMessageId: string,
): ResumeOrigin {
  const ix = useInteractionStore.getState();
  for (const e of ix.listPending(conversationId, [
    "ask_user",
    "plan_review",
    "team_preview",
  ])) {
    if (!e.origin) continue;
    if (
      !e.messageId ||
      e.messageId === resumeMessageId ||
      resolveColdResumeKey(conversationId, e.messageId) === resumeMessageId
    ) {
      return e.origin;
    }
  }
  const paused = usePausedTurnStore
    .getState()
    .pending.find(
      (p) =>
        p.conversationId === conversationId && p.messageId === resumeMessageId,
    );
  return paused?.origin ?? "server";
}

/**
 * True when a cold pending entry can paint a clickable ResumePrompt card
 * (stamped server key). Shared by marker / composer so copy never claims a
 * card that selectVisibleColdResumes would skip.
 */
export function isColdPendingDrawable(
  conversationId: string,
  entryMessageId: string,
): boolean {
  return resolveColdResumeKey(conversationId, entryMessageId) != null;
}

export function conversationHasColdPending(conversationId: string): boolean {
  return listVisibleColdResumes(conversationId).length > 0;
}

export function surfaceResumeFromLiveTurn(
  conversationId: string,
  origin: ResumeOrigin,
): void {
  const messages = getRuntime(conversationId).messages;
  const turn = [...messages].reverse().find((m) => m.role === "assistant");
  if (!turn) return;
  const user = [...messages].reverse().find((m) => m.role === "user");
  surfaceResumeFromAssistant(
    conversationId,
    { id: turn.id, serverMessageId: turn.serverMessageId },
    origin,
    { content: user?.content, id: user?.id },
  );
}
