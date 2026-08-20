import type {
  CheckpointDisplay,
  PlanReviewDisplay,
  TeamPreviewDisplay,
} from "@/stores/conversation/types";
import { useMemo } from "react";
import {
  type ApprovalView,
  entryToApproval,
  entryToCheckpoint,
  entryToPlanReview,
  entryToTeamPreview,
} from "./adapters";
import { useInteractionStore } from "./store";
import type { InteractionEntry } from "./types";

function matchesMessage(
  e: InteractionEntry,
  conversationId: string,
  messageId: string,
): boolean {
  if (e.conversationId !== conversationId) return false;
  if (!e.messageId || !messageId) return true;
  return e.messageId === messageId;
}

/**
 * Cold-path cards anchored to one assistant message (inline timeline).
 *
 * Bags are per-kind Display adapters (not flag groups): each kind has its own
 * view-model.
 */
export function useMessageInteractionCards(
  conversationId: string | null,
  messageId: string,
): {
  checkpoints: CheckpointDisplay[];
  planReviews: PlanReviewDisplay[];
  teamPreviews: TeamPreviewDisplay[];
} {
  const byId = useInteractionStore((s) => s.byId);
  return useMemo(() => {
    const checkpoints: CheckpointDisplay[] = [];
    const planReviews: PlanReviewDisplay[] = [];
    const teamPreviews: TeamPreviewDisplay[] = [];
    if (!conversationId) {
      return { checkpoints, planReviews, teamPreviews };
    }
    for (const e of byId.values()) {
      if (!matchesMessage(e, conversationId, messageId)) continue;
      if (e.kind === "ask_user") checkpoints.push(entryToCheckpoint(e));
      else if (e.kind === "plan_review") planReviews.push(entryToPlanReview(e));
      else if (e.kind === "team_preview")
        teamPreviews.push(entryToTeamPreview(e));
    }
    return { checkpoints, planReviews, teamPreviews };
  }, [byId, conversationId, messageId]);
}

/** Pending (+ submitting) approval cards for the active conversation. */
export function usePendingApprovals(
  conversationId: string | null,
): ApprovalView[] {
  const byId = useInteractionStore((s) => s.byId);
  return useMemo(() => {
    if (!conversationId) return [];
    const out: ApprovalView[] = [];
    for (const e of byId.values()) {
      if (e.conversationId !== conversationId) continue;
      if (e.kind !== "approval") continue;
      if (e.status !== "pending" && e.status !== "submitting") continue;
      out.push(entryToApproval(e));
    }
    return out;
  }, [byId, conversationId]);
}
