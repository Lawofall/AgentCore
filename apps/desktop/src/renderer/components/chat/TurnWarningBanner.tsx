import { AlertTriangle } from "lucide-react";

/** Preflight soft-gate warning shown on an assistant turn (turn_warning SSE). */
export function TurnWarningBanner({ message }: { message: string }) {
  return (
    <div
      data-testid="turn-warning-banner"
      className="mb-3 flex items-start gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
    >
      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}
