import type { ResumeDeferredBusyReason } from "@/lib/resumeDeferred";
import type { ResumeSettledTurnStatus } from "@/lib/resumeSettled";
import {
  type ResumeOrigin,
  beginPausedSnapshot,
  nextObservationSeq,
} from "@/stores/pausedTurns";
import type {
  InteractionKind,
  InteractionStatus,
} from "@/types/interactionExt";
import { create } from "zustand";
import {
  clearColdServerSettled,
  forgetColdServerSettled,
  isColdCheckpointSettled,
  noteColdServerSettled,
} from "./coldSettlement";
import {
  type ColdResumeKind,
  type InteractionEntry,
  idFromRequiredPayload,
  idFromResolvedPayload,
  isColdResumeKind,
  isHotInteractionKind,
  kindFromRequiredEvent,
  kindFromResolvedEvent,
} from "./types";

interface InteractionState {
  /** All interactions keyed by id (user-facing wire kinds). */
  byId: Map<string, InteractionEntry>;
  /** Upsert from a `*_required` SSE or recovery/journal hydrate. */
  upsertRequired: (input: {
    kind: InteractionKind;
    conversationId: string;
    messageId: string;
    payload: Record<string, unknown>;
    /** Force status (e.g. recovery hydrate → pending; journal resolved skip). */
    status?: InteractionStatus;
    /** Live SSE transport (`ctx.source`); omit on journal/recovery hydrate. */
    origin?: ResumeOrigin;
  }) => void;
  /** Mark resolved from a `*_resolved` SSE / journal. */
  markResolved: (input: {
    kind: InteractionKind;
    id: string;
    resolution?: Record<string, unknown>;
    /** 另一端拍板的（判定见 {@link InteractionEntry.settledElsewhere}）。 */
    settledElsewhere?: boolean;
  }) => void;
  /**
   * 提交回执说这张卡已经结了（见 {@link InteractionEntry.settledByReceipt}）：
   * 关掉操作面，但**不认领**结果与处理方——两者都等线材帧。
   */
  markSettledByReceipt: (input: {
    kind: InteractionKind;
    id: string;
    /** 本端没登记过这张卡时（recovery 后冷路）建桩用，别留个无主条目。 */
    conversationId?: string;
  }) => void;
  /**
   * 冷 resume 的帧已被上一次续跑吃掉（EPHEMERAL `resume_settled`）：
   * 卡收成结果态，并记下 journal 那条 settlement 说得出的事实
   * （见 {@link InteractionEntry.resumeSettled}）。处理方仍不认领。
   */
  markResumeSettled: (input: {
    /** `checkpoint_id` —— 三种冷卡的 id 字段都是它。 */
    id: string;
    kind: ColdResumeKind;
    conversationId: string;
    messageId: string;
    decision: string;
    decidedAt: string;
    turnStatus: ResumeSettledTurnStatus;
  }) => void;
  /**
   * Mark orphaned (SSE interaction_orphaned or local sidecar death).
   * When id is unknown (SSE reorder: orphaned before required), `opts.kind`
   * builds a terminal stub so a later required cannot resurrect a false pending.
   */
  markOrphaned: (
    id: string,
    opts?: {
      kind?: InteractionKind;
      conversationId?: string;
      messageId?: string;
    },
  ) => void;
  /** Flip pending → submitting (returns false if not pending). */
  beginSubmit: (id: string) => boolean;
  /**
   * Cold resume accepted while slot busy (EPHEMERAL `resume_deferred`).
   * Keeps / forces submitting so ResumePrompt stays busy until stream settles.
   */
  markResumeDeferred: (input: {
    conversationId: string;
    messageId: string;
    busyReason: ResumeDeferredBusyReason;
  }) => void;
  /** Re-open after a failed submit (not 410). */
  reopen: (id: string) => void;
  /** Drop one entry (legacy remove paths / tests). */
  remove: (id: string) => void;
  /**
   * Forget interactions at a turn / conversation boundary.
   * Prefer {@link orphanConversation} for sidecar death (灰态) over wipe.
   */
  clear: (conversationId?: string) => void;
  /** Sidecar / process death: flip hot pending cards to orphaned 灰态. */
  orphanConversation: (conversationId: string, hotOnly?: boolean) => void;
  /**
   * Replace this conversation's recovery-authoritative pending set
   * (`reconnectAnswerable` = `!isColdResumeKind`).
   * Learns `setForConversation`: a snapshot may dispose only cards it could
   * have seen (`surfacedSeq <= since`) whose origin it actually asked
   * (`confirmed`). Cloud empty pending never disposes `origin=sidecar` /
   * missing origin. Local sidecar-origin hot cards count as empty only when
   * sidecar was asked and `!sidecarLive`. Unseen hot cards the snapshot may
   * dispose are marked terminal in place (no `Map.delete`): `origin=server`
   * → {@link markResolved} with `settledElsewhere` (他端已决);
   * `origin=sidecar` → {@link markOrphaned} (帧消失). Cold kinds are never
   * written from `pending_interactions` and never disposed here — see
   * {@link settleUnseenCold}.
   */
  hydratePending: (
    conversationId: string,
    entries: Array<{
      kind: InteractionKind;
      id: string;
      messageId: string;
      payload: Record<string, unknown>;
      origin?: ResumeOrigin;
    }>,
    opts?: {
      since?: number;
      confirmed?: readonly ResumeOrigin[];
      sidecarLive?: boolean;
    },
  ) => void;
  /**
   * Cold pending the paused snapshot could have seen but didn't:
   * mark terminal in place (no `Map.delete`). Per-card — not gated on
   * the whole `paused=[]`. `origin=server` → {@link markResumeSettled}
   * (他端已决); `origin=sidecar` → {@link markOrphaned} (帧消失).
   */
  settleUnseenCold: (
    conversationId: string,
    visibleIds: ReadonlySet<string>,
    opts?: {
      since?: number;
      confirmed?: readonly ResumeOrigin[];
    },
  ) => void;
  /**
   * Re-key entry.messageId after message_start stamps the server id
   * (cold entries may have been upserted against the client bubble id).
   */
  rekeyMessageId: (fromMessageId: string, toMessageId: string) => void;
  /**
   * Bind unbound cold pending (empty messageId) to a newly stamped server
   * message id so live ResumePrompt paints without waiting for hard refresh.
   */
  bindEmptyMessageId: (conversationId: string, toMessageId: string) => void;
  get: (id: string) => InteractionEntry | undefined;
  listForConversation: (conversationId: string) => InteractionEntry[];
  listPending: (
    conversationId: string,
    kinds?: readonly InteractionKind[],
  ) => InteractionEntry[];
}

function mapCopy(
  src: Map<string, InteractionEntry>,
): Map<string, InteractionEntry> {
  return new Map(src);
}

const ALL_ORIGINS: readonly ResumeOrigin[] = ["sidecar", "server"];

/**
 * 快照只处置它看得见、且来源已确认的卡。无 origin 与未问到的来源一律留下。
 * sidecar 热卡还要本机已问到且 `!sidecarLive` 才算「空」。
 */
function snapshotCanDisposeHot(
  entry: { origin?: ResumeOrigin; surfacedSeq?: number },
  opts: {
    since: number;
    confirmed: readonly ResumeOrigin[];
    sidecarLive: boolean;
  },
): boolean {
  if ((entry.surfacedSeq ?? 0) > opts.since) return false;
  if (!entry.origin) return false;
  if (!opts.confirmed.includes(entry.origin)) return false;
  if (entry.origin === "sidecar") return !opts.sidecarLive;
  return true;
}

function snapshotCanSettleCold(
  entry: { origin?: ResumeOrigin; surfacedSeq?: number },
  opts: { since: number; confirmed: readonly ResumeOrigin[] },
): boolean {
  if ((entry.surfacedSeq ?? 0) > opts.since) return false;
  if (!entry.origin) return false;
  return opts.confirmed.includes(entry.origin);
}

export const useInteractionStore = create<InteractionState>((set, get) => ({
  byId: new Map(),

  upsertRequired: ({
    kind,
    conversationId,
    messageId,
    payload,
    status,
    origin,
  }) => {
    const id = idFromRequiredPayload(kind, payload);
    if (!id) return;
    set((state) => {
      const prev = state.byId.get(id);
      if (prev && (prev.status === "resolved" || prev.status === "orphaned")) {
        // Explicit force (recovery hydrate → pending) may replace terminal.
        const forcedPending = status === "pending";
        // Resolved stub (resolved SSE before required): empty prior payload —
        // a live required is authoritative and must paint.
        const resolvedStub =
          prev.status === "resolved" &&
          (!prev.payload || Object.keys(prev.payload).length === 0);
        // Cold: same checkpoint id on a new host message ⇒ new occurrence
        // (re-delivery of a settled card keeps the same messageId).
        const coldNewHost =
          prev.status === "resolved" &&
          isColdResumeKind(kind) &&
          Boolean(messageId) &&
          Boolean(prev.messageId) &&
          messageId !== prev.messageId;
        if (!forcedPending && !resolvedStub && !coldNewHost) {
          return {};
        }
        if (isColdResumeKind(kind)) forgetColdServerSettled(id);
      }
      // Idempotent re-delivery: keep the first pending/submitting payload.
      if (prev && (prev.status === "pending" || prev.status === "submitting")) {
        let patched = prev;
        if (messageId && !prev.messageId) {
          patched = { ...patched, messageId };
        }
        if (origin && !prev.origin) {
          patched = { ...patched, origin };
        }
        if (patched !== prev) {
          const next = mapCopy(state.byId);
          next.set(id, patched);
          return { byId: next };
        }
        return {};
      }
      const next = mapCopy(state.byId);
      next.set(id, {
        id,
        kind,
        status: status ?? "pending",
        conversationId,
        messageId: messageId || "",
        payload,
        surfacedSeq: nextObservationSeq(),
        ...(origin ? { origin } : {}),
      });
      return { byId: next };
    });
  },

  markResolved: ({ kind, id, resolution, settledElsewhere }) => {
    if (isColdResumeKind(kind)) noteColdServerSettled(id);
    set((state) => {
      const prev = state.byId.get(id);
      const next = mapCopy(state.byId);
      if (prev) {
        next.set(id, {
          ...prev,
          status: "resolved",
          resolution: resolution ?? prev.resolution,
          resumeDeferred: undefined,
          ...(settledElsewhere ? { settledElsewhere: true } : {}),
        });
      } else {
        // Resolved without a prior required (reload edge) — keep a stub so UI
        // can show 已答 if something still looks up by id.
        next.set(id, {
          id,
          kind,
          status: "resolved",
          conversationId: "",
          messageId: "",
          payload: {},
          resolution,
        });
      }
      return { byId: next };
    });
  },

  markSettledByReceipt: ({ kind, id, conversationId }) => {
    if (isColdResumeKind(kind)) noteColdServerSettled(id);
    set((state) => {
      const prev = state.byId.get(id);
      if (prev?.status === "resolved" || prev?.status === "orphaned") return {};
      const next = mapCopy(state.byId);
      next.set(id, {
        ...(prev ?? {
          id,
          kind,
          conversationId: conversationId ?? "",
          messageId: "",
          payload: {},
        }),
        status: "resolved",
        resumeDeferred: undefined,
        settledByReceipt: true,
      });
      return { byId: next };
    });
  },

  markResumeSettled: ({
    id,
    kind,
    conversationId,
    messageId,
    decision,
    decidedAt,
    turnStatus,
  }) => {
    noteColdServerSettled(id);
    set((state) => {
      const prev = state.byId.get(id);
      // 作废的卡不复活：orphan 说的是「没人能再收答复」，与本帧说的「已经有人决定过」
      // 冲突时以先落地的终态为准，别把一张灰卡改写成结果态。
      if (prev?.status === "orphaned") return {};
      const next = mapCopy(state.byId);
      next.set(id, {
        ...(prev ?? {
          id,
          kind,
          conversationId,
          messageId,
          payload: {},
        }),
        status: "resolved",
        resumeDeferred: undefined,
        resumeSettled: { decision, decidedAt, turnStatus },
      });
      return { byId: next };
    });
  },

  markOrphaned: (id, opts) => {
    const prevKind = get().byId.get(id)?.kind ?? opts?.kind;
    if (prevKind && isColdResumeKind(prevKind)) noteColdServerSettled(id);
    set((state) => {
      const prev = state.byId.get(id);
      if (prev?.status === "resolved") return {};
      const next = mapCopy(state.byId);
      if (prev) {
        next.set(id, { ...prev, status: "orphaned" });
        return { byId: next };
      }
      // Orphaned without a prior required (SSE reorder) — terminal stub so a
      // later `*_required` cannot resurrect a false pending (see upsertRequired).
      const kind = opts?.kind;
      if (!kind) return {};
      next.set(id, {
        id,
        kind,
        status: "orphaned",
        conversationId: opts?.conversationId ?? "",
        messageId: opts?.messageId ?? "",
        payload: {},
      });
      return { byId: next };
    });
  },

  beginSubmit: (id) => {
    const prev = get().byId.get(id);
    if (!prev || prev.status !== "pending") return false;
    set((state) => {
      const cur = state.byId.get(id);
      if (!cur || cur.status !== "pending") return {};
      const next = mapCopy(state.byId);
      next.set(id, { ...cur, status: "submitting" });
      return { byId: next };
    });
    return true;
  },

  markResumeDeferred: ({ conversationId, messageId, busyReason }) => {
    set((state) => {
      const next = mapCopy(state.byId);
      let changed = false;
      for (const [id, entry] of state.byId) {
        if (entry.conversationId !== conversationId) continue;
        if (!isColdResumeKind(entry.kind)) continue;
        if (entry.status !== "pending" && entry.status !== "submitting")
          continue;
        if (entry.messageId && entry.messageId !== messageId) continue;
        next.set(id, {
          ...entry,
          status: "submitting",
          messageId: entry.messageId || messageId,
          resumeDeferred: { busyReason },
        });
        changed = true;
      }
      return changed ? { byId: next } : {};
    });
  },

  reopen: (id) => {
    set((state) => {
      const prev = state.byId.get(id);
      if (!prev || prev.status !== "submitting") return {};
      // 冷卡：传输失败不得把「连接断了」读成「卡又待答」。终态判据与画卡共用。
      if (
        isColdResumeKind(prev.kind) &&
        isColdCheckpointSettled({
          checkpointId: id,
          entry: prev,
          conversationId: prev.conversationId,
        })
      ) {
        return {};
      }
      const next = mapCopy(state.byId);
      next.set(id, {
        ...prev,
        status: "pending",
        resumeDeferred: undefined,
      });
      return { byId: next };
    });
  },

  remove: (id) => {
    set((state) => {
      if (!state.byId.has(id)) return {};
      const next = mapCopy(state.byId);
      next.delete(id);
      return { byId: next };
    });
  },

  clear: (conversationId) => {
    set((state) => {
      if (conversationId === undefined) {
        clearColdServerSettled();
        return { byId: new Map() };
      }
      const next = new Map<string, InteractionEntry>();
      for (const [id, entry] of state.byId) {
        if (entry.conversationId !== conversationId) next.set(id, entry);
      }
      return { byId: next };
    });
  },

  orphanConversation: (conversationId, hotOnly = true) => {
    set((state) => {
      let changed = false;
      const next = mapCopy(state.byId);
      for (const [id, entry] of state.byId) {
        if (entry.conversationId !== conversationId) continue;
        if (entry.status !== "pending" && entry.status !== "submitting")
          continue;
        if (hotOnly && !isHotInteractionKind(entry.kind)) continue;
        next.set(id, { ...entry, status: "orphaned" });
        changed = true;
      }
      return changed ? { byId: next } : {};
    });
  },

  hydratePending: (conversationId, entries, opts) => {
    const since = opts?.since ?? beginPausedSnapshot();
    const confirmed = opts?.confirmed ?? ALL_ORIGINS;
    const sidecarLive = opts?.sidecarLive ?? false;
    const incoming = new Map<string, (typeof entries)[number]>();
    for (const e of entries) {
      if (isColdResumeKind(e.kind)) continue;
      incoming.set(e.id, e);
    }
    const toOrphan: InteractionEntry[] = [];
    const toSettle: InteractionEntry[] = [];
    for (const entry of get().byId.values()) {
      if (entry.conversationId !== conversationId) continue;
      if (entry.status !== "pending" && entry.status !== "submitting") {
        continue;
      }
      if (isColdResumeKind(entry.kind)) {
        continue;
      }
      if (incoming.has(entry.id)) continue;
      if (!snapshotCanDisposeHot(entry, { since, confirmed, sidecarLive })) {
        continue;
      }
      if (entry.origin === "sidecar") toOrphan.push(entry);
      else toSettle.push(entry);
    }
    for (const entry of toOrphan) {
      get().markOrphaned(entry.id, {
        kind: entry.kind,
        conversationId: entry.conversationId,
        messageId: entry.messageId,
      });
    }
    for (const entry of toSettle) {
      get().markResolved({
        kind: entry.kind,
        id: entry.id,
        settledElsewhere: true,
      });
    }
    if (incoming.size === 0) return;
    set((state) => {
      const next = mapCopy(state.byId);
      for (const e of incoming.values()) {
        next.set(e.id, {
          id: e.id,
          kind: e.kind,
          status: "pending",
          conversationId,
          messageId: e.messageId,
          payload: e.payload,
          surfacedSeq: nextObservationSeq(),
          ...(e.origin ? { origin: e.origin } : {}),
        });
      }
      return { byId: next };
    });
  },

  settleUnseenCold: (conversationId, visibleIds, opts) => {
    const since = opts?.since ?? beginPausedSnapshot();
    const confirmed = opts?.confirmed ?? ALL_ORIGINS;
    const toOrphan: InteractionEntry[] = [];
    const toSettle: InteractionEntry[] = [];
    for (const entry of get().byId.values()) {
      if (entry.conversationId !== conversationId) continue;
      if (!isColdResumeKind(entry.kind)) continue;
      if (entry.status !== "pending" && entry.status !== "submitting") {
        continue;
      }
      if (visibleIds.has(entry.id)) continue;
      if (!snapshotCanSettleCold(entry, { since, confirmed })) continue;
      if (entry.origin === "sidecar") toOrphan.push(entry);
      else toSettle.push(entry);
    }
    for (const entry of toOrphan) {
      get().markOrphaned(entry.id, {
        kind: entry.kind,
        conversationId: entry.conversationId,
        messageId: entry.messageId,
      });
    }
    for (const entry of toSettle) {
      if (!isColdResumeKind(entry.kind)) continue;
      get().markResumeSettled({
        id: entry.id,
        kind: entry.kind,
        conversationId: entry.conversationId,
        messageId: entry.messageId,
        decision: "",
        decidedAt: "",
        turnStatus: "unknown",
      });
    }
  },

  rekeyMessageId: (fromMessageId, toMessageId) => {
    if (!fromMessageId || !toMessageId || fromMessageId === toMessageId) return;
    set((state) => {
      let changed = false;
      const next = mapCopy(state.byId);
      for (const [id, entry] of state.byId) {
        if (entry.messageId !== fromMessageId) continue;
        next.set(id, { ...entry, messageId: toMessageId });
        changed = true;
      }
      return changed ? { byId: next } : {};
    });
  },

  /**
   * Bind cold pending entries that arrived before message_start stamped a
   * durable resume key (empty messageId) to the new server message id so
   * ResumePrompt can paint as soon as the stamp lands.
   */
  bindEmptyMessageId: (conversationId, toMessageId) => {
    if (!conversationId || !toMessageId) return;
    set((state) => {
      let changed = false;
      const next = mapCopy(state.byId);
      for (const [id, entry] of state.byId) {
        if (entry.conversationId !== conversationId) continue;
        if (entry.messageId) continue;
        if (entry.status !== "pending" && entry.status !== "submitting")
          continue;
        if (!isColdResumeKind(entry.kind)) continue;
        next.set(id, { ...entry, messageId: toMessageId });
        changed = true;
      }
      return changed ? { byId: next } : {};
    });
  },

  get: (id) => get().byId.get(id),

  listForConversation: (conversationId) => {
    const out: InteractionEntry[] = [];
    for (const entry of get().byId.values()) {
      if (entry.conversationId === conversationId) out.push(entry);
    }
    return out;
  },

  listPending: (conversationId, kinds) => {
    const out: InteractionEntry[] = [];
    for (const entry of get().byId.values()) {
      if (entry.conversationId !== conversationId) continue;
      if (entry.status !== "pending" && entry.status !== "submitting") continue;
      if (kinds && !kinds.includes(entry.kind)) continue;
      out.push(entry);
    }
    return out;
  },
}));

/**
 * 本端这一路从没结掉过它——归属才可能落在别人身上。
 *
 * 「回执说已经结了」也算没结掉：那一下本端并未成为处理方（服务端明说是别人先结的），而回执
 * 证不了是谁，所以操作面虽已关掉，归属仍留给随后带线材字段的那帧去证。
 */
function localNeverSettled(prev: InteractionEntry | undefined): boolean {
  if (!prev) return false;
  if (prev.status === "pending") return true;
  return prev.status === "resolved" && prev.settledByReceipt === true;
}

/**
 * 这一帧的收口是**有人**答的吗？
 *
 * 「本端没提交过」离「另一端的人拍的」还差一步：有些收口根本没有人参与——升级卡可由 CEO
 * 裁决，或按假设推进 / 超时兜底，线材里 `status` 与 `arbitrated_by` 说得明明白白。这类要
 * 是也算到用户头上，就成了替他认领一个他没做过的动作。其余几类的 `*_resolved` 今天只有
 * 「人答了」这一个生产者（冷卡的收口帧出自 resume 路，热审批出自决策路）。
 */
function answeredByAPerson(
  kind: InteractionKind,
  payload: Record<string, unknown>,
): boolean {
  if (kind !== "escalation") return true;
  if (payload.status !== "resolved") return false; // assumed / timed_out = 运行时兜底
  return !payload.arbitrated_by; // CEO 裁决（含 via_user：人答的是 CEO 的问，不是这张卡）
}

/**
 * Apply a required/resolved/orphaned wire event into the store.
 *
 * ``opts.live`` = 这帧是刚发生的转折（实时 SSE，非 catch-up 重放段、非 journal 水合）。
 * 只有它为真时，一帧落在仍 pending 条目上的 ``*_resolved`` 才判为「另一端拍板」。
 */
export function applyInteractionWireEvent(
  eventType: string,
  payload: Record<string, unknown>,
  conversationId: string,
  messageId: string,
  origin?: ResumeOrigin,
  opts?: { live?: boolean },
): boolean {
  const store = useInteractionStore.getState();

  if (eventType === "interaction_orphaned") {
    const id =
      typeof payload.interaction_id === "string"
        ? payload.interaction_id
        : null;
    const kind =
      typeof payload.kind === "string"
        ? (payload.kind as InteractionKind)
        : undefined;
    if (id) {
      store.markOrphaned(id, { kind, conversationId, messageId });
    }
    return true;
  }

  const requiredKind = kindFromRequiredEvent(eventType);
  if (requiredKind) {
    store.upsertRequired({
      kind: requiredKind,
      conversationId,
      messageId,
      payload,
      ...(origin ? { origin } : {}),
    });
    return true;
  }

  const resolvedKind = kindFromResolvedEvent(eventType);
  if (resolvedKind) {
    const id = idFromResolvedPayload(resolvedKind, payload);
    if (id) {
      // 本端从没结掉过它（提交会先 beginSubmit 到 submitting，或已乐观 markResolved）
      // ——那这一下就是另一端**的人**拍的。
      const settledElsewhere =
        opts?.live === true &&
        localNeverSettled(store.get(id)) &&
        answeredByAPerson(resolvedKind, payload);
      store.markResolved({
        kind: resolvedKind,
        id,
        resolution: payload,
        settledElsewhere,
      });
    }
    return true;
  }

  return false;
}

/** Hydrate InteractionStore from a message's journal events (reload path). */
export function hydrateInteractionsFromJournal(
  conversationId: string,
  messageId: string,
  events: Array<{ type: string; payload: unknown }>,
): void {
  for (const ev of events) {
    applyInteractionWireEvent(
      ev.type,
      (ev.payload ?? {}) as Record<string, unknown>,
      conversationId,
      messageId,
    );
  }
}
