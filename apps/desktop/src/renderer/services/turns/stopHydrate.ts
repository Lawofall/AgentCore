import { logEvent } from "@/lib/log";
import { loadLatestWindow } from "@/services/messages";
import { finalizeHonestStopAbort } from "@/services/turns/helpers";
import { getTurnPhase } from "@/stores/conversation/turnPhaseActions";

/** Live ``message_end`` usually arrives first; hydrate if the stream was already gone. */
export const STOP_HYDRATE_MS = 4000;

const stopHydrateTimers = new Map<string, ReturnType<typeof setTimeout>>();

export function clearStopHydrateWatchdog(conversationId: string): void {
  const timer = stopHydrateTimers.get(conversationId);
  if (timer === undefined) return;
  clearTimeout(timer);
  stopHydrateTimers.delete(conversationId);
}

export function resetStopHydrateWatchdogForTests(): void {
  for (const id of [...stopHydrateTimers.keys()]) {
    clearStopHydrateWatchdog(id);
  }
}

/**
 * After Stop RPC succeeds, do not wait forever for live ``message_end``.
 * If still ``stopping`` after {@link STOP_HYDRATE_MS}, soft-refresh the window
 * and settle the composer — refresh-to-self-heal is not the product.
 */
export function armStopHydrateWatchdog(conversationId: string): void {
  clearStopHydrateWatchdog(conversationId);
  const schedule =
    typeof globalThis.setTimeout === "function"
      ? globalThis.setTimeout.bind(globalThis)
      : null;
  if (!schedule) {
    void settleStoppingFromHydrate(conversationId);
    return;
  }
  const timer = schedule(() => {
    stopHydrateTimers.delete(conversationId);
    void settleStoppingFromHydrate(conversationId);
  }, STOP_HYDRATE_MS);
  stopHydrateTimers.set(conversationId, timer);
}

export async function settleStoppingFromHydrate(
  conversationId: string,
): Promise<void> {
  if (getTurnPhase(conversationId) !== "stopping") return;
  logEvent("info", "conversation.stop_hydrate", {
    conversation_id: conversationId,
  });
  try {
    await loadLatestWindow(conversationId, { softRefresh: true });
  } catch (err: unknown) {
    logEvent("warn", "conversation.stop_hydrate_failed", {
      conversation_id: conversationId,
      error_name: err instanceof Error ? err.name : "unknown",
    });
  }
  if (getTurnPhase(conversationId) !== "stopping") return;
  finalizeHonestStopAbort(conversationId);
}
