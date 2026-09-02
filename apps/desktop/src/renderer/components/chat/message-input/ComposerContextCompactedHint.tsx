/**
 * Composer-only half of compaction chrome: ``context_gap``.
 *
 * Success (``context_compacted``) is a timeline divider at the fold watermark —
 * {@link import("../CompactionDivider").CompactionDivider} — not this line. Gap is
 * about the next send (the model really cannot see early turns), so it stays
 * above the input. Grey, never a red card: nothing failed for the user's turn
 * and nothing was deleted.
 *
 * Reported even when folding never wrote a summary: that day-long production
 * failure is the shape that most needs saying out loud.
 */
import { useConversations } from "@/hooks/useConversations";
import { composerContextGapHint } from "@/lib/composerContextCompactedHint";
import { useConversationStore } from "@/stores/conversation";

export function ComposerContextCompactedHint() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const conversations = useConversations();
  const gapHint = composerContextGapHint(
    conversationId
      ? conversations.find((c) => c.id === conversationId)?.contextGap
      : undefined,
  );

  if (!gapHint) return null;
  return (
    <div
      aria-live="polite"
      data-testid="composer-context-gap-hint"
      className="flex items-start gap-1.5 px-4 pt-2 text-xs text-muted-foreground"
    >
      {gapHint}
    </div>
  );
}
