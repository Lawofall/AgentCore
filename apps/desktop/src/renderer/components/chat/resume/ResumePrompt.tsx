import { selectVisibleColdResumes } from "@/services/resume";
import { useConversationStore } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { type PendingResume, usePausedTurnStore } from "@/stores/pausedTurns";
import type { ComponentType } from "react";
import { AskUserResumeCard } from "./AskUserResumeCard";
import { PlanReviewResumeCard } from "./PlanReviewResumeCard";
import { TeamPreviewResumeCard } from "./TeamPreviewResumeCard";

/** Cold-path pending cards only (`pausesTurn && !hot` / COLD_RESUME_KINDS). */
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
  // Cold-path Interaction kinds (`submitPathOf` → "cold" / COLD_RESUME_KINDS).
  const Card = COLD_RESUME_CARDS[turn.kind];
  return <Card turn={turn} />;
}

/** Cold-path resume cards — one component per `COLD_RESUME_KINDS` member (UI, not a flag bag). */
const COLD_RESUME_CARDS: Record<
  "ask_user" | "plan_review" | "team_preview",
  ComponentType<{ turn: PendingResume }>
> = {
  ask_user: AskUserResumeCard,
  plan_review: PlanReviewResumeCard,
  team_preview: TeamPreviewResumeCard,
};
