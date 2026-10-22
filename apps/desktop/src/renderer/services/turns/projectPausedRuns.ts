/**
 * Project pause-frame display runs onto the assistant bubble + execution store.
 *
 * Local sidecar pause writeback skips cloud ``turn_journal``, so GET messages
 * often has ``runs=null`` while a durable pause card is pending. Recovery
 * carries ``pausedRuns`` from the on-disk frame (pinned at pause save); this
 * helper fills the collab-graph journal without attach-replaying a live turn.
 */
import { ensureTimelineMarkersFromJournal } from "@/lib/foldMessageLane";
import { completeProcessTranscript } from "@/lib/processTimeline";
import {
  assistantProjectionId,
  getRuntime,
  useConversationStore,
} from "@/stores/conversation";
import { type ExecutionJournal, useExecutionStore } from "@/stores/execution";
import { hydrateInteractionsFromJournal } from "@/stores/interactions";
import type { ProcessStep, SSEEvent } from "@/types/events";
import type { SidecarRunsPayload } from "@shared/sidecar-contract";

function executionIdOf(events: SSEEvent[]): string | null {
  const plan = events.find((e) => e.type === "run_plan");
  const id = (plan?.payload as { execution_id?: string } | undefined)
    ?.execution_id;
  return id ?? null;
}

function asSSEEvents(raw: Record<string, unknown>[] | undefined): SSEEvent[] {
  if (!raw || raw.length === 0) return [];
  return raw
    .filter(
      (e): e is Record<string, unknown> & { type: string } =>
        !!e && typeof e === "object" && typeof e.type === "string",
    )
    .map((e) => e as unknown as SSEEvent);
}

/**
 * Enrich assistants that lack (or have thinner) ``runs`` from local pause frames.
 * No-op when ``pausedRuns`` is empty or every target already has a richer journal.
 */
export function projectPausedRuns(
  conversationId: string,
  pausedRuns: Record<string, SidecarRunsPayload>,
): void {
  const ids = Object.keys(pausedRuns);
  if (ids.length === 0) return;

  const store = useConversationStore.getState();
  // Conversation-scoped updateMessage — do not switch the open conversation.
  for (const [messageId, runs] of Object.entries(pausedRuns)) {
    const events = asSSEEvents(runs.events);
    if (events.length === 0) continue;
    const executionId = executionIdOf(events);
    if (!executionId) continue;

    const rt = getRuntime(conversationId);
    const msg = rt.messages.find(
      (m) =>
        m.role === "assistant" &&
        (m.id === messageId || m.serverMessageId === messageId),
    );
    if (!msg) continue;

    const existingLen = msg.runs?.events?.length ?? 0;
    if (existingLen >= events.length) continue;

    const finishReason = runs.finish_reason ?? "paused";
    const runProcesses = (runs.run_processes ?? null) as Record<
      string,
      ProcessStep[]
    > | null;
    const journal: ExecutionJournal = {
      events,
      finishReason,
      runProcesses,
    };

    // Same projection key as InlineTeamGraph / SSE (`serverMessageId ?? id`).
    // Never write the client bubble id when serverMessageId is already stamped.
    const slotKey = assistantProjectionId(msg);
    hydrateInteractionsFromJournal(conversationId, slotKey, events);
    useExecutionStore.getState().hydrateFromJournal(slotKey, journal);

    const baseProcess: ProcessStep[] | undefined =
      (runs.process as ProcessStep[] | null | undefined) ?? undefined;
    const marked = ensureTimelineMarkersFromJournal(baseProcess, events);
    const process =
      msg.content && msg.content.length > 0
        ? completeProcessTranscript(marked, msg.content)
        : marked;

    // Conversation bubble lookup is still by client `msg.id`; slot keys above
    // are projection-scoped only (no dual-write / no post-hoc slot copy).
    store.updateMessage(
      msg.id,
      {
        executionId,
        runs: journal,
        process,
        finishReason: "paused",
        isStreaming: false,
        ...(msg.serverMessageId ? {} : { serverMessageId: messageId }),
      },
      conversationId,
    );
  }
}
