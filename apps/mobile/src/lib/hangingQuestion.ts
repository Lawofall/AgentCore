import { type NonBlockingAsk, extractAsks } from "@/protocol/fold";
import type {
  QuestionResolvedPayload,
  SSEEvent,
} from "@agentcore/contract-types";

/**
 * 挂问标题 / CTA / 默认假设成文在 kit。下面的 collect / detached 谓词读 fold
 * 与 SSE，属于本端实现，不进共享。
 */
export {
  HANGING_QUESTION_CAPTION,
  HANGING_QUESTION_CTA,
  HANGING_QUESTION_DEFAULT_HINT,
  HANGING_QUESTION_DETACHED_HINT,
  formatHangingDefault,
} from "@agentcore/protocol-fold-kit";

/**
 * Apply `question_resolved` from a list onto already-posted asks.
 * `extractAsks` only settles when posted+resolved share a list; another list
 * can carry only the resolved event (live vs history, turn A vs turn B).
 */
function applyResolvedFromEvents(
  byId: Map<string, NonBlockingAsk>,
  events: readonly SSEEvent[],
): void {
  for (const ev of events) {
    if (ev.type !== "question_resolved") continue;
    const p = ev.payload as QuestionResolvedPayload;
    const id = typeof p.ask_id === "string" ? p.ask_id : "";
    const prev = byId.get(id);
    if (!prev || prev.status !== "pending") continue;
    byId.set(id, {
      ...prev,
      status: "resolved",
      settlement: p.status,
      ...(p.answer ? { answer: p.answer } : {}),
      ...(p.note ? { note: p.note } : {}),
    });
  }
}

/** Conversation-wide pending hanging questions. No cap. Any list can settle an id. */
export function collectPendingHangingQuestions(
  eventLists: readonly SSEEvent[][],
): NonBlockingAsk[] {
  const byId = new Map<string, NonBlockingAsk>();
  const order: string[] = [];
  for (const events of eventLists) {
    for (const ask of extractAsks(events)) {
      const prev = byId.get(ask.id);
      if (!prev) {
        order.push(ask.id);
        byId.set(ask.id, ask);
        continue;
      }
      if (prev.status === "pending" && ask.status !== "pending") {
        byId.set(ask.id, ask);
      }
    }
  }
  // Second pass: resolved-only lists never produce an extractAsks row.
  for (const events of eventLists) {
    applyResolvedFromEvents(byId, events);
  }
  return order
    .map((id) => byId.get(id))
    .filter((ask): ask is NonBlockingAsk => ask?.status === "pending");
}

/**
 * Detached hint for the current live team graph only.
 * Do not pass history windows — a past `execution_detached` must not light
 * a current hanging question.
 */
export function eventsHaveExecutionDetached(
  currentGraphEvents: readonly SSEEvent[],
): boolean {
  return currentGraphEvents.some((ev) => ev.type === "execution_detached");
}
