import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export interface EmptyHintProps {
  title: string;
  /** Second line — what to do next, or why it's empty. */
  hint?: string;
  icon?: ReactNode;
  /** Optional primary action (新建 / 去某处). */
  action?: ReactNode;
  /** Fill the host (`h-full`) instead of growing with the page (`flex-1`). */
  inline?: boolean;
  className?: string;
}

/**
 * List / grid page empty state: title + optional hint + optional action.
 * Chat draft empty stays `DraftEmptyState`. Sidebar / drawer one-liners stay
 * one-liners — do not wrap those in this centered block.
 */
export function EmptyHint({
  title,
  hint,
  icon,
  action,
  inline,
  className,
}: EmptyHintProps) {
  return (
    <output
      className={cn(
        "flex flex-col items-center justify-center gap-2 px-6 text-center",
        inline ? "h-full" : "flex-1",
        className,
      )}
    >
      {icon}
      <p className="text-sm text-muted-foreground">{title}</p>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      {action}
    </output>
  );
}
