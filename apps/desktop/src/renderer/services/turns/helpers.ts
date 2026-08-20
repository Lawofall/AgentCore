import { StreamError } from "@/lib/errors";
import {
  type Message,
  getActiveRuntime,
  getRuntime,
  useConversationStore,
} from "@/stores/conversation";
import {
  completeTurnPhase,
  getTurnPhase,
} from "@/stores/conversation/turnPhaseActions";
import { usePausedTurnStore } from "@/stores/pausedTurns";

/** The user's explicit stop (abort button) — never surfaced as an error. */
export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/**
 * Clear composer lock when a turn path dies while still marked generating.
 * Shared by runSend / regenerate / stage-card resolve — same catch/abort 收口.
 */
export function finalizeGeneratingIfNeeded(conversationId: string): void {
  if (getRuntime(conversationId).isGenerating) {
    useConversationStore.getState().finalizeLastMessage(conversationId);
  }
}

/**
 * Honest-stop Abort 收口：RPC 可能先于 ``message_end`` reject。
 * ``stopping`` → ``stopped``，清 ``isGenerating``，并盖 ``finishReason=cancelled``
 * （原先只清 ``isStreaming``，条会假「进行中」直到刷新）。
 * Shared by sendTurn / resume / regenerate / rejoin / stage-card（midFlight 除外：
 * Stop ≠ 取消排队）。
 */
export function finalizeHonestStopAbort(conversationId: string): void {
  const wasStopping = getTurnPhase(conversationId) === "stopping";
  if (wasStopping) {
    completeTurnPhase(conversationId, "stopped");
  }
  finalizeGeneratingIfNeeded(conversationId);
  if (wasStopping) stampHonestStopCancelled(conversationId);
}

/** Cover finishReason so StatusStrip / hydrate see user-stop, not a dangling stream. */
function stampHonestStopCancelled(conversationId: string): void {
  const store = useConversationStore.getState();
  const tail = getRuntime(conversationId).messages.at(-1);
  if (!tail || tail.role !== "assistant") return;
  // Empty interrupt / pause keep their own faces; do not rewrite to cancelled.
  if (tail.finishReason === "paused" || tail.finishReason === "interrupted") {
    return;
  }
  store.updateMessage(
    tail.id,
    {
      isStreaming: false,
      finishReason: "cancelled",
      runs: tail.runs ? { ...tail.runs, finishReason: "cancelled" } : tail.runs,
    },
    conversationId,
  );
}

/**
 * Cold pending pause card ⇒ conversation must not look「仍在生成」.
 * Clears isGenerating + closes the tail assistant stream (stamp finishReason=paused
 * when the active slice can be patched). Call after painting/merging cold cards
 * (loadRecovery / surfaceResume) or on settle hold (paused≥1).
 *
 * @param force — settle hold already knows paused≥1 from recovery snap; clear even
 *   if pausedTurns is not hydrated yet (stale-empty race).
 */
export function finalizeGeneratingForPausedConversation(
  conversationId: string,
  options?: { force?: boolean },
): void {
  const hasPaused = usePausedTurnStore
    .getState()
    .pending.some((p) => p.conversationId === conversationId);
  if (!options?.force && !hasPaused) return;

  const store = useConversationStore.getState();
  const rt = getRuntime(conversationId);
  const last = rt.messages.at(-1);
  if (rt.isGenerating || last?.isStreaming) {
    store.finalizeLastMessage(conversationId);
  }

  // Stamp paused on the tail assistant (hydrate-equivalent close). Must update
  // the *target* conversation even when another chat is open.
  const tail = getRuntime(conversationId).messages.at(-1);
  if (
    !tail ||
    tail.role !== "assistant" ||
    tail.finishReason === "paused" ||
    tail.finishReason === "interrupted"
  ) {
    return;
  }
  store.updateMessage(
    tail.id,
    {
      isStreaming: false,
      finishReason: "paused",
      runs: tail.runs ? { ...tail.runs, finishReason: "paused" } : tail.runs,
    },
    conversationId,
  );
}

/** A mid-stream transport drop (socket died), as opposed to a backend refusal
 * (quota / rate limit / missing key, which never started a run). Only a drop
 * warrants RECONNECT — since 执行与请求解耦 (C1 · slice 1a) the turn keeps running
 * detached, so the right recovery is to rejoin it (1b), not resend it. */
export function isTransportDrop(err: unknown): boolean {
  return err instanceof StreamError && err.kind === "network";
}

/** Quiet — backoff attach is still running; no recovery verdict yet. */
export const RECONNECTING_BANNER = "连接中断，正在重连…";

/**
 * Confirmed live via ``loadRecovery`` (``live_running`` / sidecar live).
 * Quiet reconnect, not the old false-promise「稍后查看即可接上」.
 */
export const RECONNECT_LIVE_BANNER = "连接中断，回合仍在继续，正在重连…";

/** Confirmed settled: persisted assistant is complete. */
export const RECONNECT_FINISHED_BANNER =
  "连接曾中断，回合已完成。可直接查看结果。";

/** Confirmed settled: no complete reply (dead lease / empty / interrupted). */
export const RECONNECT_INTERRUPTED_BANNER =
  "连接中断，本回合未能完成。可重新发送继续。";

/**
 * @deprecated Prefer {@link RECONNECT_LIVE_BANNER}. Kept as the same string so
 * existing equality checks / reopen settle stay aligned.
 */
export const RECONNECT_BANNER = RECONNECT_LIVE_BANNER;

/**
 * zh banner when recovery could not confirm cloud live/idle (``!cloudKnown``).
 * Not a transport drop — do not reuse live/finished/interrupted copy. Never ghost,
 * never resend; reopen / later settle may refresh facts (no one-click banner action).
 */
export const UNKNOWN_CLOUD_BANNER =
  "暂时无法确认回合状态。请稍后再试或重新打开对话。";

const RECONNECT_RETRY_BANNERS = new Set<string>([
  RECONNECTING_BANNER,
  RECONNECT_LIVE_BANNER,
  UNKNOWN_CLOUD_BANNER,
]);

/** Banners that mean "still trying to rejoin" — markOnline / reopen may wake. */
export function isReconnectRetryBanner(
  message: string | null | undefined,
): boolean {
  return Boolean(message && RECONNECT_RETRY_BANNERS.has(message));
}

const RECONNECT_QUIET_BANNERS = new Set<string>([
  RECONNECTING_BANNER,
  RECONNECT_LIVE_BANNER,
  RECONNECT_FINISHED_BANNER,
]);

/**
 * Reconnect copy that is not a confirmed-bad outcome — RetryBanner uses the
 * existing Info + {@link noticeChipNeutral} notice chrome (not the triangle).
 */
export function isReconnectQuietBanner(
  message: string | null | undefined,
): boolean {
  return Boolean(message && RECONNECT_QUIET_BANNERS.has(message));
}

/** The latest user message of a conversation's slice, or null. */
export function lastUserMessageOf(conversationId: string): Message | null {
  const msgs = getRuntime(conversationId).messages;
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === "user") return msgs[i];
  }
  return null;
}

/** The most recent user message in the open conversation, or null. Backs the
 * task card's retry / adjust-instruction / replan actions. */
export function lastUserMessage(): Message | null {
  const msgs = getActiveRuntime().messages;
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === "user") return msgs[i];
  }
  return null;
}

/** Id of {@link lastUserMessage}, or null. */
export function lastUserMessageId(): string | null {
  return lastUserMessage()?.id ?? null;
}
