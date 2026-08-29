import {
  assistantProjectionId,
  getRuntime,
  lastAssistantProjectionId,
} from "@/stores/conversation";
import type { ProcessStep } from "@/types/events";

/** Optional routing hint from an SSE payload. */
export type ExecRouteHint = {
  /**
   * Explicit host slot (`host_message_id` / `host_turn_id`).
   * Kept for `execution_detached` / `execution_completed` and old-journal payloads —
   * not a sticky cross-turn divert.
   */
  host_message_id?: string;
  execution_id?: string;
};

/** Resolve a server/client message id to the execution slot key (`serverMessageId ?? id`). */
export function resolveExecSlotId(
  conversationId: string,
  messageRef: string,
): string {
  const ref = messageRef.trim();
  if (!ref) return ref;
  const messages = getRuntime(conversationId).messages;
  const hit = messages.find((m) => m.id === ref || m.serverMessageId === ref);
  return hit ? assistantProjectionId(hit) : ref;
}

/** Find the host assistant bubble for an execution (message.executionId or process markers). */
export function findHostSlotForExecution(
  conversationId: string,
  executionId: string,
): string | null {
  const eid = executionId.trim();
  if (!eid) return null;
  const messages = getRuntime(conversationId).messages;
  for (const m of messages) {
    if (m.role !== "assistant") continue;
    if (m.executionId === eid) return assistantProjectionId(m);
    const team = m.process?.find(
      (s): s is Extract<ProcessStep, { kind: "team" }> =>
        s.kind === "team" && s.execution_id === eid,
    );
    if (team) return assistantProjectionId(m);
  }
  return null;
}

/**
 * Resolve the execution slot for **growth** facts (run_plan / run_* / worker tools…).
 *
 * Priority: explicit host slot (`host_message_id` / `host_turn_id`) → same
 * `execution_id` host lookup (same-turn merge) → latest assistant.
 *
 * Cross-turn divert (sticky growth → previous bubble) was removed: new turns open
 * their own graph; `prev_execution_id` remains a protocol chain, not a user-facing
 * back-link.
 */
export function execMessageId(
  conversationId: string,
  hint?: ExecRouteHint | null,
): string | null {
  const hostHint =
    typeof hint?.host_message_id === "string"
      ? hint.host_message_id.trim()
      : "";
  if (hostHint) return resolveExecSlotId(conversationId, hostHint);

  const eid =
    typeof hint?.execution_id === "string" ? hint.execution_id.trim() : "";
  if (eid) {
    const mapped = findHostSlotForExecution(conversationId, eid);
    if (mapped) return mapped;
  }

  return lastAssistantProjectionId(getRuntime(conversationId).messages);
}

/** CEO-lane slot (latest assistant) — never diverted to a host graph. */
export function ceoMessageId(conversationId: string): string | null {
  return lastAssistantProjectionId(getRuntime(conversationId).messages);
}

/**
 * Stamped server message id for assistants after the latest user message
 * (same turn). Walks newest-first so a stray unstamped tail does not hide the
 * durable host from an earlier pause in the same turn.
 */
export function sameTurnStampedServerId(conversationId: string): string | null {
  const messages = getRuntime(conversationId).messages;
  let lastUserIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") {
      lastUserIdx = i;
      break;
    }
  }
  for (let i = messages.length - 1; i > lastUserIdx; i--) {
    const m = messages[i];
    if (m.role === "assistant" && m.serverMessageId) return m.serverMessageId;
  }
  return null;
}

/**
 * Bind key for cold `*_required` upsert: same-turn stamped server id, else "".
 * Never pin pending to a client-only bubble id (ResumePrompt gate requires stamp).
 */
export function coldBindMessageId(conversationId: string): string {
  return sameTurnStampedServerId(conversationId) ?? "";
}

/** Pull routing fields from an opaque SSE payload. */
export function routeHintFromPayload(payload: unknown): ExecRouteHint | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;
  // `host_turn_id` is the execution_detached / execution_completed host key.
  const host =
    typeof p.host_message_id === "string"
      ? p.host_message_id
      : typeof p.host_turn_id === "string"
        ? p.host_turn_id
        : undefined;
  const execution =
    typeof p.execution_id === "string" ? p.execution_id : undefined;
  if (!host && !execution) return null;
  return { host_message_id: host, execution_id: execution };
}
