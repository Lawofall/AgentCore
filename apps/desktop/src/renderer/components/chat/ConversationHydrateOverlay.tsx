import { Button } from "@/components/ui";
import { Loader2 } from "lucide-react";

export type ConversationHydratePhase = "loading" | "ready" | "error";

/**
 * Full-pane honest shell for persisted-conversation hydrate (诚实壳层 A).
 * Covers the conversation pane so a cold load never looks like an empty draft you can send into,
 * and a failed fetch without offline cache never looks like a blank conversation.
 */
export function ConversationHydrateOverlay({
  phase,
  onRetry,
}: {
  phase: ConversationHydratePhase;
  onRetry?: () => void;
}) {
  if (phase === "ready") return null;

  if (phase === "loading") {
    return (
      <output
        className="absolute inset-0 z-30 flex items-center justify-center gap-2 bg-background text-sm text-muted-foreground"
        aria-live="polite"
        aria-label="正在加载对话"
      >
        <Loader2 size={14} className="animate-spin" />
        正在加载对话…
      </output>
    );
  }

  return (
    <div
      className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-3 bg-background px-6"
      role="alert"
    >
      <p className="text-sm text-muted-foreground">对话加载失败</p>
      <Button variant="primary" onClick={onRetry}>
        重试
      </Button>
    </div>
  );
}
