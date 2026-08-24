/**
 * Project unsynced sidecar outbox summaries into the conversation slice (D5).
 *
 * Adapts each summary into BackendMessage shape → `toMessage` → `addMessage`.
 * Cloud window is primary: skip matching ids unless unsynced is strictly richer
 * (`messageRichnessScore`). Marks ready rows synced_pending.
 */
import { type BackendMessage, toMessage } from "@/services/messages";
import {
  getRuntime,
  messageIdentityKeys,
  messageRichnessScore,
  useConversationStore,
} from "@/stores/conversation";
import type { Message } from "@/stores/conversation/types";
import type { ProcessStep, SSEEvent, UsageBreakdown } from "@/types/events";
import type { SidecarUnsyncedTurnSummary } from "@shared/sidecar-contract";

function createdAtIso(updatedAt: number): string {
  // Outbox `updated_at` is Unix seconds (Python time.time()).
  const ms = updatedAt > 1e12 ? updatedAt : updatedAt * 1000;
  return new Date(ms || Date.now()).toISOString();
}

function summaryToBackendMessages(
  conversationId: string,
  u: SidecarUnsyncedTurnSummary,
): BackendMessage[] {
  const created = createdAtIso(u.updated_at);
  const status =
    u.phase === "open" ? ("incomplete" as const) : ("complete" as const);
  const hasUsage =
    u.input_tokens ||
    u.output_tokens ||
    u.reasoning_tokens ||
    u.cache_hit_tokens ||
    u.cache_miss_tokens;
  const usage: UsageBreakdown | null = hasUsage
    ? ({
        input: u.input_tokens,
        output: u.output_tokens,
        reasoning: u.reasoning_tokens,
        cache_hit: u.cache_hit_tokens,
        cache_miss: u.cache_miss_tokens,
      } as UsageBreakdown)
    : null;

  const user: BackendMessage = {
    id: u.user_message_id,
    conversation_id: conversationId,
    role: "user",
    content: u.user_message,
    reasoning_content: null,
    trace_id: u.trace_id || null,
    created_at: created,
  };

  const assistantId = u.message_id || `assistant-${u.user_message_id}`;
  const events = (u.runs?.events ?? []) as unknown as SSEEvent[];
  const process = (u.runs?.process ?? null) as ProcessStep[] | null;
  const runProcesses = (u.runs?.run_processes ?? null) as Record<
    string,
    ProcessStep[]
  > | null;
  const assistant: BackendMessage = {
    id: assistantId,
    conversation_id: conversationId,
    role: "assistant",
    content: u.content,
    reasoning_content: u.reasoning_content,
    trace_id: u.trace_id || null,
    citations: u.citations?.length
      ? u.citations.map((c) => ({
          url: c.url,
          title: c.title,
          snippet: c.snippet,
          site: c.site,
        }))
      : undefined,
    runs: u.runs
      ? {
          events,
          finish_reason: u.runs.finish_reason ?? u.finish_reason ?? null,
          process,
          run_processes: runProcesses,
        }
      : u.finish_reason
        ? { events: [], finish_reason: u.finish_reason }
        : null,
    usage,
    status,
    created_at: created,
  };

  return [user, assistant];
}

function findMessageByIdentity(
  messages: Message[],
  needle: Message,
): Message | undefined {
  const keys = new Set(messageIdentityKeys(needle));
  return messages.find((m) => messageIdentityKeys(m).some((k) => keys.has(k)));
}

/**
 * Append unsynced outbox turns (sorted by updated_at ascending by recovery).
 * Idempotent on message id unless unsynced is strictly richer than cloud.
 */
export function projectUnsyncedTurns(
  conversationId: string,
  unsynced: SidecarUnsyncedTurnSummary[],
): void {
  if (!unsynced.length) return;
  const store = useConversationStore.getState();
  const existing = new Set(
    getRuntime(conversationId).messages.map((m) => m.id),
  );

  for (const u of unsynced) {
    const rows = summaryToBackendMessages(conversationId, u);
    for (const row of rows) {
      const msg = toMessage(row);
      // Open ghost (sidecar died mid-turn): surface as interrupted, not streaming.
      // Empty cancelled/dead: keep terminal finish (cancelled → synthetic cancelled
      // face; blank dead → interrupted「已中断」) so product face is never blank.
      if (row.role === "assistant") {
        const empty = !(msg.content ?? "").trim();
        if (u.phase === "open" && msg.status === "incomplete") {
          msg.isStreaming = false;
          msg.finishReason = msg.finishReason ?? "interrupted";
        } else if (empty && !msg.error?.message?.trim()) {
          const fr = u.finish_reason ?? msg.finishReason;
          if (fr === "cancelled") {
            msg.isStreaming = false;
            msg.status = "incomplete";
            msg.finishReason = "cancelled";
            if (msg.runs) {
              msg.runs = { ...msg.runs, finishReason: "cancelled" };
            }
          } else if (u.phase === "dead" || (!fr && u.phase === "ready")) {
            msg.isStreaming = false;
            msg.status = "incomplete";
            msg.finishReason = "interrupted";
            if (msg.runs) {
              msg.runs = { ...msg.runs, finishReason: "interrupted" };
            }
          }
        }
      }

      const resident = findMessageByIdentity(
        getRuntime(conversationId).messages,
        msg,
      );
      if (resident) {
        const incomingScore = messageRichnessScore(msg);
        const residentScore = messageRichnessScore(resident);
        if (incomingScore <= residentScore) continue;
        const { id: _drop, ...patch } = msg;
        store.updateMessage(resident.id, patch, conversationId);
        continue;
      }
      if (existing.has(row.id)) continue;

      store.addMessage(msg, conversationId);
      existing.add(row.id);
    }
    if (u.phase === "ready" || u.phase === "dead") {
      store.setTurnSyncStatus(
        u.user_message_id,
        "synced_pending",
        conversationId,
      );
    }
  }
}
