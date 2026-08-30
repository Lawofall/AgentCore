import { cn } from "@/lib/utils";
import type { HTMLAttributes, ReactNode } from "react";
import {
  type DecisionShellTone,
  decisionAccentText,
  decisionCtaBarAll,
  decisionShell,
} from "./tone-presets";

/** Semantic tone for boss-decision cards (ask_user / plan_review / approval). */
export type DecisionTone = DecisionShellTone;

export interface DecisionCardProps extends HTMLAttributes<HTMLDivElement> {
  tone?: DecisionTone;
  /** Play the one-shot task-card entrance animation. */
  animate?: boolean;
}

/** Pattern shell for inline boss-decision cards in the chat timeline. */
export function DecisionCard({
  tone = "primary",
  animate = false,
  className,
  ...props
}: DecisionCardProps) {
  return (
    <div
      className={cn(
        "mt-2 rounded-xl border p-3",
        decisionShell[tone],
        animate && "animate-task-card-enter",
        className,
      )}
      {...props}
    />
  );
}

export function DecisionCardFooter({
  tone = "primary",
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { tone?: DecisionTone }) {
  return (
    <div
      className={cn(
        "mt-3 flex flex-wrap items-center justify-end gap-2.5 border-t px-3 py-2.5",
        decisionCtaBarAll[tone],
        className,
      )}
      {...props}
    />
  );
}

export function DecisionCardIcon({
  className,
  tone = "primary",
  children,
}: {
  tone?: DecisionTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn("mt-0.5 shrink-0", decisionAccentText[tone], className)}
    >
      {children}
    </span>
  );
}
