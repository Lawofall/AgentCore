import { IconButton } from "@/components/ui";
import { cn } from "@/lib/utils";
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";

export interface CanvasShellProps {
  /** Accessible name for the back IconButton. */
  backAriaLabel: string;
  onBack: () => void;
  /** Title control (plain text or an editable field). */
  title: ReactNode;
  /** Muted status word (saving / version). */
  status?: ReactNode;
  /** Page-level actions on the right of the top bar. */
  actions?: ReactNode;
  /** Optional strip under the top bar (conflict / errors). Does not squeeze the canvas. */
  banner?: ReactNode;
  /** Remaining space — the canvas fills this. */
  children: ReactNode;
  className?: string;
}

/**
 * Deep-page chrome for 白板 / 工作流画布 inside AppShell main.
 * Does not own the engine or any in-canvas toolbar.
 */
export function CanvasShell({
  backAriaLabel,
  onBack,
  title,
  status,
  actions,
  banner,
  children,
  className,
}: CanvasShellProps) {
  return (
    <div className={cn("absolute inset-0 flex flex-col", className)}>
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-background px-3">
        <IconButton aria-label={backAriaLabel} onClick={onBack}>
          <ArrowLeft size={16} />
        </IconButton>
        {title}
        <span className="ml-auto text-xs text-muted-foreground">{status}</span>
        {actions ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
          </div>
        ) : null}
      </header>
      {banner}
      <div className="relative min-h-0 flex-1">{children}</div>
    </div>
  );
}
