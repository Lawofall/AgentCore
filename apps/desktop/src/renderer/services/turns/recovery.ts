import { describeStreamError, streamErrorAction } from "@/lib/errors";
import { logEvent } from "@/lib/log";
import { loadLatestWindow } from "@/services/messages";
import { type ConversationRecovery, loadRecovery } from "@/services/resume";
import {
  attachConversation,
  clearLastEventId,
} from "@/services/streamConversation";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import { beginTurnPreflight } from "@/stores/conversation/turnPhaseActions";
import { useExecutionStore } from "@/stores/execution";
import { clearInteractionPrompts } from "@/stores/interactionPrompts";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import { setServerHealthRecoveredHandler } from "@/stores/serverHealth";
import {
  RECONNECTING_BANNER,
  RECONNECT_FINISHED_BANNER,
  RECONNECT_INTERRUPTED_BANNER,
  RECONNECT_LIVE_BANNER,
  UNKNOWN_CLOUD_BANNER,
  finalizeGeneratingForPausedConversation,
  finalizeHonestStopAbort,
  isAbort,
  isReconnectRetryBanner,
  isTransportDrop,
  lastUserMessageOf,
} from "./helpers";
import { reconnectBackoffMs } from "./reconnectBackoff";
import { hasLocalConversationStream } from "./streamOwnership";

type RejoinOnceResult = "ok" | "empty" | "retry" | "abort";

type RejoinSlot = {
  conversationId: string;
  stopped: boolean;
  attempts: number;
  ac: AbortController | null;
  wake: (() => void) | null;
  unsubStore: (() => void) | null;
};

const rejoinSlots = new Map<string, RejoinSlot>();

function wakeSlot(slot: RejoinSlot): void {
  slot.wake?.();
}

function sleepRejoin(slot: RejoinSlot, ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    const timer = setTimeout(() => {
      slot.wake = null;
      resolve();
    }, ms);
    slot.wake = () => {
      clearTimeout(timer);
      slot.wake = null;
      resolve();
    };
  });
}

function stopRejoinSlot(slot: RejoinSlot, reason: string): void {
  if (slot.stopped) return;
  slot.stopped = true;
  slot.unsubStore?.();
  slot.unsubStore = null;
  slot.ac?.abort();
  slot.ac = null;
  wakeSlot(slot);
  if (rejoinSlots.get(slot.conversationId) === slot) {
    rejoinSlots.delete(slot.conversationId);
  }
  logEvent("info", "conversation.rejoin_closed", {
    conversation_id: slot.conversationId,
    reason,
  });
}

/** Why this slot must stop — empty string = keep retrying. */
function rejoinStopReason(slot: RejoinSlot): string {
  const state = useConversationStore.getState();
  if (!state.byId[slot.conversationId]) return "slice_dropped";
  if (state.currentConversationId !== slot.conversationId) {
    return "window_closed";
  }
  const rt = getRuntime(slot.conversationId);
  if (rt.turnPhase === "stopping") return "user_stop";
  // Our own attach holds the local-stream gate — only yield when idle
  // (backoff) and someone else opened a POST / attach.
  if (!slot.ac && hasLocalConversationStream(slot.conversationId)) {
    return "local_stream";
  }
  if (rt.abort && rt.abort !== slot.ac) return "user_takeover";
  return "";
}

function watchRejoinTakeover(slot: RejoinSlot): void {
  slot.unsubStore = useConversationStore.subscribe(() => {
    if (slot.stopped) return;
    const reason = rejoinStopReason(slot);
    if (reason) stopRejoinSlot(slot, reason);
  });
}

/** Abort in-flight / scheduled attach retries so a user send / stop / resume can take over. */
export function cancelRejoinLiveTurn(conversationId: string): void {
  const slot = rejoinSlots.get(conversationId);
  if (slot) stopRejoinSlot(slot, "cancelled");
}

/** Skip remaining backoff. Returns true when a retry slot was waiting. */
export function wakeRejoinLiveTurn(conversationId: string): boolean {
  const slot = rejoinSlots.get(conversationId);
  if (!slot || slot.stopped) return false;
  wakeSlot(slot);
  return true;
}

export function resetRejoinLiveTurnForTests(): void {
  for (const slot of [...rejoinSlots.values()])
    stopRejoinSlot(slot, "test_reset");
  rejoinSlots.clear();
}

export function handleServerHealthRecovered(): void {
  const conv = useConversationStore.getState();
  const bannerIds: string[] = [];
  for (const [id, rt] of Object.entries(conv.byId)) {
    if (isReconnectRetryBanner(rt.error)) bannerIds.push(id);
  }
  for (const id of bannerIds) conv.clearError(id);
  for (const slot of rejoinSlots.values()) wakeSlot(slot);
  for (const id of bannerIds) {
    if (!rejoinSlots.has(id)) void rejoinLiveTurn(id);
  }
}

setServerHealthRecoveredHandler(handleServerHealthRecovered);

/** Cold-load pause latch is ``status=running`` + ``finishReason=paused``. */
function isPausedFinish(message: {
  finishReason?: string;
  runs?: { finishReason?: string } | null;
}): boolean {
  return (
    message.finishReason === "paused" || message.runs?.finishReason === "paused"
  );
}

function isFinishedAssistant(message: {
  role: string;
  status?: string | null;
  content?: string;
  finishReason?: string;
  runs?: { finishReason?: string } | null;
}): boolean {
  if (message.role !== "assistant") return false;
  if (message.status === "complete") return true;
  const fr = message.finishReason ?? message.runs?.finishReason;
  if (
    fr === "interrupted" ||
    fr === "error" ||
    fr === "cancelled" ||
    fr === "unproductive" ||
    fr === "paused"
  ) {
    return false;
  }
  return (
    Boolean((message.content ?? "").trim()) && message.status !== "running"
  );
}

/**
 * After attach 204 / recovery says idle: reload already applied. Speak from
 * the persisted tail — no extra poller.
 */
function applySettledWindowBanner(conversationId: string): "ok" | "empty" {
  const store = useConversationStore.getState();
  const last = getRuntime(conversationId).messages.at(-1);
  if (last?.role === "assistant" && isPausedFinish(last)) {
    store.clearError(conversationId);
    finalizeGeneratingForPausedConversation(conversationId, { force: true });
    return "ok";
  }
  if (last?.role === "assistant" && last.status === "running") {
    markGhostInterrupted(conversationId);
  }
  const tail = getRuntime(conversationId).messages.at(-1);
  if (tail && isFinishedAssistant(tail)) {
    store.setError(RECONNECT_FINISHED_BANNER, null, conversationId, null);
    return "ok";
  }
  store.setError(RECONNECT_INTERRUPTED_BANNER, null, conversationId, null);
  return tail?.role === "assistant" ? "ok" : "empty";
}

/**
 * Reuse ``loadRecovery`` + ``loadLatestWindow`` (hydrate / reopen path) to
 * decide copy. Never a new poller — one snapshot per failed attach.
 */
async function settleDroppedTurnFromRecovery(
  conversationId: string,
): Promise<"retry" | "stop"> {
  const store = useConversationStore.getState();
  try {
    const snap = await loadRecovery(conversationId);
    if (snap.pausedCount > 0) {
      store.clearError(conversationId);
      finalizeGeneratingForPausedConversation(conversationId, { force: true });
      return "stop";
    }
    if (snap.sidecarLive || snap.cloudLive) {
      store.setError(RECONNECT_LIVE_BANNER, null, conversationId, null);
      return "retry";
    }
    if (!snap.cloudKnown && !snap.sidecarLive) {
      store.setError(UNKNOWN_CLOUD_BANNER, null, conversationId, null);
      return "retry";
    }
    store.setGenerating(false, conversationId);
    await loadLatestWindow(conversationId);
    applySettledWindowBanner(conversationId);
    return "stop";
  } catch {
    // Snapshot / window reload failed — never leave the quiet "重连中" face.
    store.setError(UNKNOWN_CLOUD_BANNER, null, conversationId, null);
    return "retry";
  }
}

/**
 * Settle a live execution slot to cancelled so {@link finalizeFold} freezes
 * in-flight nodes. No-op when there is no plan (avoids inventing empty slots).
 */
function finalizeRunningExecutionSlots(
  messageId: string,
  serverMessageId?: string | null,
): void {
  const exec = useExecutionStore.getState();
  const settle = (id: string) => {
    const rt = exec.byId[id];
    if (!rt?.plan) return;
    if (
      rt.status === "running" ||
      rt.status === "planning" ||
      rt.status === "paused"
    ) {
      exec.setStatus("cancelled", id);
    }
  };
  settle(messageId);
  if (serverMessageId && serverMessageId !== messageId) {
    settle(serverMessageId);
  }
}

/**
 * One GET attach (never POST / resend). Shared by the first drop and bounded
 * retries — a resend would double-run a turn that is still alive server-side.
 */
async function attemptRejoinOnce(
  conversationId: string,
  ac: AbortController,
  opts: { silentAbort: boolean; keepBanner: boolean },
): Promise<RejoinOnceResult> {
  const lastUser = lastUserMessageOf(conversationId);
  if (!lastUser) return "empty";

  const store = useConversationStore.getState();
  if (!opts.keepBanner) store.clearError(conversationId);

  store.setAbort(ac, conversationId);
  beginTurnPreflight(conversationId);
  try {
    const outcome = await attachConversation(conversationId, ac.signal);
    if (outcome === "attached") {
      store.clearError(conversationId);
      return "ok";
    }
    // No live run — the detached turn already finished + persisted. Reload it so
    // the saved reply replaces whatever partial we still hold. Clear generating
    // first so the whole-window write gate does not reject the reload.
    useConversationStore.getState().setGenerating(false, conversationId);
    await loadLatestWindow(conversationId);
    return applySettledWindowBanner(conversationId);
  } catch (err) {
    if (isAbort(err)) {
      if (!opts.silentAbort) finalizeHonestStopAbort(conversationId);
      return "abort";
    }
    const s = useConversationStore.getState();
    if (getRuntime(conversationId).isGenerating) {
      s.finalizeLastMessage(conversationId);
    }
    clearInteractionPrompts(conversationId);
    if (!isTransportDrop(err)) {
      const msg = describeStreamError(err);
      if (msg) {
        s.setError(msg, null, conversationId, streamErrorAction(err));
      }
      return "ok";
    }
    // Quiet while we ask the existing recovery snapshot — never a "querying" face.
    if (
      !opts.keepBanner ||
      isReconnectRetryBanner(getRuntime(conversationId).error)
    ) {
      s.setError(RECONNECTING_BANNER, null, conversationId, null);
    }
    const verdict = await settleDroppedTurnFromRecovery(conversationId);
    return verdict === "retry" ? "retry" : "ok";
  } finally {
    if (getRuntime(conversationId).abort === ac) {
      useConversationStore.getState().setAbort(null, conversationId);
    }
  }
}

async function runRejoinLoop(slot: RejoinSlot): Promise<void> {
  watchRejoinTakeover(slot);
  // No attempt cap: a live turn can run 10+ minutes. Stay on the 1s→30s
  // curve (storm risk is the first seconds) until the server says the run
  // is gone, attach holds, the chat window closes, or the user takes over.
  // One slot + one timer per conversation; stopRejoinSlot always unsubs.
  while (!slot.stopped) {
    const yieldReason = rejoinStopReason(slot);
    if (yieldReason) {
      stopRejoinSlot(slot, yieldReason);
      return;
    }
    const delay = reconnectBackoffMs(slot.attempts);
    logEvent("info", "conversation.rejoin_retry", {
      conversation_id: slot.conversationId,
      attempt: slot.attempts + 1,
      delay_ms: delay,
    });
    slot.attempts += 1;
    await sleepRejoin(slot, delay);
    if (slot.stopped) return;
    const afterSleep = rejoinStopReason(slot);
    if (afterSleep) {
      stopRejoinSlot(slot, afterSleep);
      return;
    }
    const ac = new AbortController();
    slot.ac = ac;
    const result = await attemptRejoinOnce(slot.conversationId, ac, {
      silentAbort: true,
      keepBanner: true,
    });
    if (slot.ac === ac) slot.ac = null;
    if (slot.stopped) return;
    if (result === "retry") continue;
    stopRejoinSlot(
      slot,
      result === "ok" ? "reattached" : result === "empty" ? "none" : "abort",
    );
    return;
  }
}

function startRejoinLoop(conversationId: string): void {
  const existing = rejoinSlots.get(conversationId);
  if (existing && !existing.stopped) {
    wakeSlot(existing);
    return;
  }
  const slot: RejoinSlot = {
    conversationId,
    stopped: false,
    attempts: 0,
    ac: null,
    wake: null,
    unsubStore: null,
  };
  rejoinSlots.set(conversationId, slot);
  void runRejoinLoop(slot);
}

/**
 * Rejoin a turn whose live stream dropped mid-flight (实时重连续看 C1 · slice 1b).
 *
 * Post-decoupling (slice 1a) a dropped connection no longer kills a turn — it
 * runs detached + persists — so a transport drop must RECONNECT, not resend (a
 * resend / regenerate would double-run a turn that is still alive). Attaches as-is:
 * replay + live tail. On `"none"` the run already finished — reload the persisted
 * transcript (its reply is saved). If reconnect itself drops, surface a banner
 * and keep GET-attaching on the same 1s→30s backoff as conversation follow
 * until the run settles, the chat window closes, or the user takes over.
 * Never POST.
 *
 * **不在这里清屏。** 手上这半场要不要抹，由 attach 段首的 ``full_replay`` 说了算
 * （``streamConversation.foldAttachSegment``）：服务端认得我们的游标时只补游标之后的
 * 事实，抢先清掉上半场就再也补不回来了——那正是掉线重连后回合前半段永久消失的成因。
 *
 * Returns `true` when handled (reattached / reloaded a saved reply / banner shown);
 * `false` only when there is no turn to rejoin and nothing was persisted, so the
 * caller can fall back to its resend / regenerate banner.
 */
export async function rejoinLiveTurn(conversationId: string): Promise<boolean> {
  const pending = rejoinSlots.get(conversationId);
  if (pending && !pending.stopped) {
    wakeSlot(pending);
    return true;
  }

  const ac = new AbortController();
  const result = await attemptRejoinOnce(conversationId, ac, {
    silentAbort: false,
    keepBanner: false,
  });
  if (result === "empty") return false;
  if (result === "retry") {
    startRejoinLoop(conversationId);
    return true;
  }
  return true;
}

/**
 * Mark a dead-lease ghost (``usage.status=running`` but recovery has no live run
 * and no pause) as interrupted so the bubble stops spinning. Empty body → layer 1
 * recoverability (send a new turn / composer hint + synthetic「已中断」face); no
 * message-level retry row.
 *
 * No-op when the tail is already paused (``finishReason`` / ``runs.finishReason``)
 * — a successful pause must not be rewritten as interrupted.
 *
 * Also drops any resume card painted from a stale ``usage.paused`` latch + journal
 * residual (ask_user fact still in journal after the user already continued).
 */
export function markGhostInterrupted(conversationId: string): void {
  const store = useConversationStore.getState();
  const last = getRuntime(conversationId).messages.at(-1);
  if (!last || last.role !== "assistant" || last.status !== "running") return;
  // Successful pause (cold-load latch) must not be rewritten as interrupted.
  if (isPausedFinish(last)) return;
  store.updateMessage(
    last.id,
    {
      isStreaming: false,
      status: "incomplete",
      finishReason: "interrupted",
      runs: last.runs
        ? { ...last.runs, finishReason: "interrupted" }
        : last.runs,
    },
    conversationId,
  );
  useConversationStore.getState().setGenerating(false, conversationId);
  clearLastEventId(conversationId);
  // Freeze the graph in place (cancelled → finalizeFold freezes in-flight nodes).
  // Do not clearExecution — the inline team graph must stay until journal replay.
  finalizeRunningExecutionSlots(last.id, last.serverMessageId);
  // Stale latch may have painted ResumePrompt via toMessage → surfaceResume; clear it.
  const resumeKey = last.serverMessageId ?? last.id;
  usePausedTurnStore.getState().remove(resumeKey);
  clearInteractionPrompts(conversationId);
}

/**
 * Orphan empty-assistant settle (1a69f9dc · 方案 A).
 *
 * When a new turn starts (or hydrate finishes), any prior empty assistant that
 * never completed (streaming / running / abandoned incomplete) must not stay as
 * a blank product face. Rewrite to ``interrupted`` so
 * {@link syntheticErrorForEmptyFailure} paints「已中断」; do not hard-block input.
 *
 * Leaves ``cancelled`` / ``error`` / ``unproductive`` alone (those already have
 * product faces). Skips assistants with body or a real error payload.
 * Skips ``paused`` entirely — even ``status===running`` (cold-load latch).
 */
export function settleOrphanEmptyAssistants(conversationId: string): void {
  const store = useConversationStore.getState();
  const msgs = getRuntime(conversationId).messages;
  for (const m of msgs) {
    if (m.role !== "assistant") continue;
    if ((m.content ?? "").trim()) continue;
    if (m.error?.message?.trim()) continue;
    if (m.runs?.error?.message?.trim()) continue;
    // Successful pause must not become interrupted, even while still ``running``.
    if (isPausedFinish(m)) continue;
    const fr = m.finishReason ?? m.runs?.finishReason;
    // Already has a synthesizable terminal finish — keep it.
    if (
      fr === "cancelled" ||
      fr === "error" ||
      fr === "unproductive" ||
      fr === "interrupted"
    ) {
      if (!m.isStreaming && m.status !== "running") continue;
    }
    const needsSettle =
      m.isStreaming ||
      m.status === "running" ||
      m.status === "incomplete" ||
      // Settled blank with no finish (abandoned placeholder before message_end).
      (!fr && m.status !== "complete" && m.status !== "failed");
    if (!needsSettle) continue;
    store.updateMessage(
      m.id,
      {
        isStreaming: false,
        status: "incomplete",
        finishReason: "interrupted",
        runs: m.runs ? { ...m.runs, finishReason: "interrupted" } : m.runs,
      },
      conversationId,
    );
    // Freeze any live graph; do not wipe the projection (graph stays on screen).
    finalizeRunningExecutionSlots(m.id, m.serverMessageId);
  }
}

/**
 * Cloud-path settle for a last assistant with ``status===running``.
 *
 * Open-time hydrate races ``loadRecovery`` against ``fetchMessageWindow``;
 * recovery often finishes first. A cold pause that lands in between leaves a
 * stale empty snapshot (``!cloudLive ∧ pausedCount===0``) while the message
 * window still shows ``running`` — marking ghost then would wipe the pause
 * latch. Re-fetch once when the snapshot looks empty (sidecarAttach
 * ``attached:false`` precedent), then decide on the fresh facts:
 *
 * - live ∧ paused=0 → rejoin
 * - paused≥1 → hold + clear generating/streaming (card + isGenerating is illegal)
 * - cloudKnown ∧ !live ∧ paused=0 → real dead-lease / TTL degrade → ghost
 *   unless the tail is already paused (cold-load latch) — then hold, never ghost
 * - !cloudKnown → unknown (request failed); never ghost — hold; keep a prior
 *   non-empty non-{@link UNKNOWN_CLOUD_BANNER} error, else set that banner
 *   (plain banner; never resend; not {@link RECONNECT_LIVE_BANNER})
 */
export async function settleCloudRunningAssistant(
  conversationId: string,
  recovery: ConversationRecovery,
): Promise<"rejoin" | "ghost" | "hold"> {
  const store = useConversationStore.getState();
  // Do not clearError at entry — a prior concrete banner (e.g. stream drop)
  // must survive an unknown-cloud hold. Clear only on branches that change
  // turn state (rejoin / ghost / paused finalize).

  let snap = recovery;
  // Empty or unknown cloud facts: one refresh before deciding (same race as pause).
  if ((!snap.cloudKnown || !snap.cloudLive) && snap.pausedCount === 0) {
    snap = await loadRecovery(conversationId);
  }
  if (snap.cloudLive && snap.pausedCount === 0) {
    store.clearError(conversationId);
    void rejoinLiveTurn(conversationId);
    return "rejoin";
  }
  if (!snap.cloudKnown) {
    // Failure ≠ confirmed idle — leave the running assistant alone. Keep a
    // prior non-empty, non-UNKNOWN banner; otherwise explain honestly (not
    // "连接中断"). No one-click re-settle from the banner.
    const prior = (getRuntime(conversationId).error ?? "").trim();
    if (!prior || prior === UNKNOWN_CLOUD_BANNER) {
      store.setError(UNKNOWN_CLOUD_BANNER, null, conversationId, null);
    }
    return "hold";
  }
  if (!snap.cloudLive && snap.pausedCount === 0) {
    const last = getRuntime(conversationId).messages.at(-1);
    if (last?.role === "assistant" && isPausedFinish(last)) {
      store.clearError(conversationId);
      finalizeGeneratingForPausedConversation(conversationId, { force: true });
      return "hold";
    }
    store.clearError(conversationId);
    markGhostInterrupted(conversationId);
    return "ghost";
  }
  // paused≥1: force clear even if pausedTurns lag the recovery snap.
  store.clearError(conversationId);
  finalizeGeneratingForPausedConversation(conversationId, { force: true });
  return "hold";
}

/**
 * On opening a conversation, rejoin a live run or mark a dead ghost interrupted
 * (P4 unified hydrate · 实时重连续看 C1 · slice 1b). Empty interrupted → layer 1
 * (send new turn); not a resume affordance.
 *
 * - Last message is user + liveRunning → bare attach (``message_start`` opens bubble).
 * - Last message is running assistant + liveRunning → rejoin; the attach 段首 decides
 *   whether that partial is reset or continued (never double-folded).
 * - Last message is running assistant but no live / pause → ghost → interrupted.
 */
export async function attachOnOpen(conversationId: string): Promise<void> {
  const store = useConversationStore.getState();
  if (hasLocalConversationStream(conversationId)) return;
  if (wakeRejoinLiveTurn(conversationId)) return;

  const ac = new AbortController();
  store.setAbort(ac, conversationId);
  beginTurnPreflight(conversationId);
  try {
    await attachConversation(conversationId, ac.signal);
  } catch (err) {
    if (isAbort(err)) {
      finalizeHonestStopAbort(conversationId);
      return;
    }
    const s = useConversationStore.getState();
    // Only surface a reconnect banner if a bubble actually opened (a run was live
    // and we lost it); a pre-event drop / 204 stays silent.
    if (getRuntime(conversationId).isGenerating) {
      s.finalizeLastMessage(conversationId);
      s.setError(RECONNECTING_BANNER, null, conversationId, null);
      startRejoinLoop(conversationId);
    }
  } finally {
    useConversationStore.getState().setAbort(null, conversationId);
  }
}
