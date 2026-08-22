import { selectVisibleColdResumes } from "@/services/resume";
import { useConversationStore } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { type PendingResume, usePausedTurnStore } from "@/stores/pausedTurns";
import type { ComponentType } from "react";
import { AskUserResumeCard } from "./AskUserResumeCard";
import { PlanReviewResumeCard } from "./PlanReviewResumeCard";

/** Cold-path pending cards only (`pausesTurn && !hot` / COLD_RESUME_KINDS).
 * leftover `team_preview` is recognized by fold / IX but not painted. */
export function ResumePrompt() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  // Live authority = InteractionStore cold pending; pausedTurns = recovery shell.
  const byId = useInteractionStore((s) => s.byId);
  const pausedPending = usePausedTurnStore((s) => s.pending);
  const messages = useConversationStore((s) =>
    conversationId ? (s.byId?.[conversationId]?.messages ?? []) : [],
  );
  const visible = conversationId
    ? selectVisibleColdResumes({
        conversationId,
        byId,
        pausedPending,
        messages,
      })
    : [];
  if (visible.length === 0) return null;

  return (
    <div className="mx-4 mb-2 space-y-2">
      {visible.map((turn) => (
        <ResumeCard
          key={`${turn.messageId}:${turn.checkpointId}`}
          turn={turn}
        />
      ))}
    </div>
  );
}

function ResumeCard({ turn }: { turn: PendingResume }) {
  if (turn.kind === "team_preview") return null;
  const Card = COLD_RESUME_CARDS[turn.kind];
  return <Card turn={turn} />;
}

/** Operable cold-path resume cards — leftover team_preview has no continue/cancel shell. */
const COLD_RESUME_CARDS: Record<
  "ask_user" | "plan_review",
  ComponentType<{ turn: PendingResume }>
> = {
  ask_user: AskUserResumeCard,
  plan_review: PlanReviewResumeCard,
};
