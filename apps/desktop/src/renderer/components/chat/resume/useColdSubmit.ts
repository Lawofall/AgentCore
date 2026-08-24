import type { ResumeDeferredBusyReason } from "@/lib/resumeDeferred";
import { notifyError } from "@/lib/toast";
import {
  notifySubmitInteractionResult,
  submitInteraction,
} from "@/services/interactionSubmit";
import type { PlanReviewUserDecision } from "@/services/planReview";
import { useInteractionStore } from "@/stores/interactions";
import type { PendingResume } from "@/stores/pausedTurns";
import { useState } from "react";

/** Shared cold-path submit hook for plan_review resume cards. */
export function useColdSubmit(
  turn: PendingResume,
  onSubmitted?: (decision: PlanReviewUserDecision) => void,
) {
  const [submitting, setSubmitting] = useState<PlanReviewUserDecision | null>(
    null,
  );
  const entryStatus = useInteractionStore(
    (s) => s.byId.get(turn.checkpointId)?.status,
  );
  const deferredBusyReason: ResumeDeferredBusyReason | null =
    useInteractionStore(
      (s) => s.byId.get(turn.checkpointId)?.resumeDeferred?.busyReason ?? null,
    );
  // Deferred keeps busy even if local state remounts — settlement already locked.
  const busy =
    submitting !== null ||
    entryStatus === "submitting" ||
    deferredBusyReason !== null;

  const send = (
    decision: PlanReviewUserDecision,
    selected: string[] = [],
    note = "",
  ) => {
    if (busy) return;
    setSubmitting(decision);
    void submitInteraction({
      id: turn.checkpointId,
      kind: turn.kind,
      conversationId: turn.conversationId,
      cold: {
        messageId: turn.messageId,
        decision,
        note,
        selected,
      },
    })
      .then((result) => {
        if (result !== "ok") {
          notifySubmitInteractionResult(result);
          setSubmitting(null);
          return;
        }
        onSubmitted?.(decision);
        setSubmitting(null);
      })
      .catch((err) => {
        notifyError(err, "提交失败");
        setSubmitting(null);
      });
  };

  return {
    submitting,
    busy,
    deferredBusyReason,
    send,
  };
}
