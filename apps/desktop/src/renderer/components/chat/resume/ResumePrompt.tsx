import { useChatPaneId } from "@/lib/chatPane";
import { selectVisibleColdResumes } from "@/services/resume";
import { useConversationStore } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { type PendingResume, usePausedTurnStore } from "@/stores/pausedTurns";
import type { ComponentType } from "react";
import { AskUserResumeCard } from "./AskUserResumeCard";

/** Zustand getSnapshot must return a cached empty — a fresh `[]` loops React. */
const EMPTY_MESSAGES: { id: string; role: string }[] = [];

/** Cold-path pending cards only (`pausesTurn && !hot` / COLD_RESUME_KINDS). */
export function ResumePrompt() {
  const conversationId = useChatPaneId();
  // Live authority = InteractionStore cold pending with origin; pausedTurns =
  // still-waiting recovery frames. Journal required is not enough to paint.
  const byId = useInteractionStore((s) => s.byId);
  const pausedPending = usePausedTurnStore((s) => s.pending);
  const recoveryState = usePausedTurnStore((s) =>
    conversationId
      ? (s.openRecovery?.[conversationId] ?? "unresolved")
      : "unresolved",
  );
  const messages = useConversationStore((s) => {
    if (!conversationId) return EMPTY_MESSAGES;
    return s.byId?.[conversationId]?.messages ?? EMPTY_MESSAGES;
  });
  const visible = conversationId
    ? selectVisibleColdResumes({
        conversationId,
        byId,
        pausedPending,
        messages,
        recoveryState,
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
  if (turn.kind !== "ask_user") return null;
  const Card = COLD_RESUME_CARDS[turn.kind];
  return <Card turn={turn} />;
}

/** Operable cold-path resume cards. */
const COLD_RESUME_CARDS: Record<
  "ask_user",
  ComponentType<{ turn: PendingResume }>
> = {
  ask_user: AskUserResumeCard,
};
