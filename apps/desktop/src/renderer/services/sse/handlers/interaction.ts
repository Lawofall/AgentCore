import { useConversationStore } from "@/stores/conversation";
import { frameFromEvent, useExecutionStore } from "@/stores/execution";
import {
  type TimelineProcessKind,
  applyInteractionWireEvent,
  defFromRequiredEvent,
  defFromResolvedEvent,
  defFromTimelineProcess,
  interactionChannelEventTypes,
  isColdResumeKind,
  kindFromRequiredEvent,
  useInteractionStore,
  wireFor,
} from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import type { SSEEvent } from "@/types/events";
import {
  type InteractionOrphanedPayload,
  isInteractionOrphanedEvent,
} from "@/types/interactionExt";
import { flushPendingContent } from "../contentBuffer";
import { flushPendingFrames } from "../execFrameBuffer";
import { coldBindMessageId, execMessageId } from "../helpers";
import type { DispatchContext } from "../types";

const INTERACTION_SSE_TYPES = interactionChannelEventTypes();

function wireIntoInteractionStore(
  event: SSEEvent,
  conversationId: string,
  origin: DispatchContext["source"],
  live: boolean,
): void {
  // Cold pause cards are CEO-lane: never bind via growth-graph host lookup
  // alone when a same-turn stamp exists, or ResumePrompt keys the wrong turn.
  // Prefer same-turn stamped server id; never pin to an unstamped client UUID
  // (empty → bindEmptyMessageId on later message_start).
  const requiredKind = kindFromRequiredEvent(event.type);
  const messageId =
    requiredKind && isColdResumeKind(requiredKind)
      ? coldBindMessageId(conversationId)
      : (execMessageId(conversationId) ?? "");
  applyInteractionWireEvent(
    event.type,
    (event.payload ?? {}) as Record<string, unknown>,
    conversationId,
    messageId,
    origin,
    { live },
  );
}

function stampByProcessKind(
  processKind: TimelineProcessKind,
  id: string,
  conversationId: string,
): void {
  const store = useConversationStore.getState();
  const def = defFromTimelineProcess(processKind);
  if (def?.timeline) {
    store.stampTimelineMarker(def.timeline, id, conversationId);
    return;
  }
  // Fallback for kinds that still expose dedicated stamp helpers in tests.
  switch (processKind) {
    case "checkpoint":
      store.stampCheckpointMarker(id, conversationId);
      break;
    case "plan_review":
      store.stampPlanReviewMarker(id, conversationId);
      break;
    default:
      break;
  }
}

export function handleInteractionEvent(
  event: SSEEvent,
  ctx: DispatchContext,
): boolean {
  const { conversationId } = ctx;
  const live = ctx.replay !== true;

  const leftoverType = event.type as string;
  if (
    leftoverType === "team_preview_required" ||
    leftoverType === "team_preview_resolved"
  ) {
    return true;
  }

  if (isInteractionOrphanedEvent(event.type)) {
    const p = event.payload as InteractionOrphanedPayload;
    useInteractionStore.getState().markOrphaned(p.interaction_id, {
      kind: p.kind,
      conversationId,
      messageId: execMessageId(conversationId) ?? "",
    });
    return true;
  }

  if (!INTERACTION_SSE_TYPES.has(event.type)) {
    return false;
  }

  const requiredDef = defFromRequiredEvent(event.type);
  if (requiredDef) {
    const effects = requiredDef.sseRequired;
    if (effects?.flushBuffers) {
      flushPendingContent(conversationId);
      flushPendingFrames(conversationId);
    }
    wireIntoInteractionStore(event, conversationId, ctx.source, live);

    if (requiredDef.timeline) {
      const wire = wireFor(requiredDef.kind);
      const id = (event.payload as Record<string, unknown>)?.[wire.idField];
      if (typeof id === "string" && id.length > 0) {
        stampByProcessKind(
          requiredDef.timeline.processKind,
          id,
          conversationId,
        );
      }
    }

    if (effects?.recordExecFrame) {
      const mid = execMessageId(conversationId);
      const frame = frameFromEvent(event);
      if (mid && frame) useExecutionStore.getState().recordFrame(frame, mid);
    }
    return true;
  }

  const resolvedDef = defFromResolvedEvent(event.type);
  if (resolvedDef) {
    wireIntoInteractionStore(event, conversationId, ctx.source, live);
    const effects = resolvedDef.sseResolved;
    const wire = wireFor(resolvedDef.kind);
    const id = (event.payload as Record<string, unknown>)?.[wire.idField];

    if (effects?.removePausedTurn && typeof id === "string" && id.length > 0) {
      usePausedTurnStore.getState().removeByCheckpoint(id);
    }
    if (effects?.flushFrames) {
      flushPendingFrames(conversationId);
    }
    if (effects?.recordExecFrame) {
      const mid = execMessageId(conversationId);
      const frame = frameFromEvent(event);
      if (mid && frame) useExecutionStore.getState().recordFrame(frame, mid);
    }
    return true;
  }

  return false;
}
