import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
} from "lucide-react";

/**
 * Org-chart disclosure on the parent card. Sits on the midpoint of the
 * child-facing edge — the same axis the connector leaves — and must not
 * enlarge the ELK footprint.
 *
 * Chevron follows that axis: collapsed points toward the hidden members,
 * expanded points back into the card. A downward chevron in the corner reads
 * as a dropdown, which this control is not.
 */
export function SubTeamFoldChip({
  count,
  expanded,
  horizontal,
  onToggle,
}: {
  count: number;
  expanded: boolean;
  horizontal: boolean;
  onToggle?: () => void;
}) {
  const label = expanded ? `收起子队（${count}）` : `展开子队（${count}）`;
  const position = horizontal
    ? "right-0 top-1/2 translate-x-1/2 -translate-y-1/2"
    : "bottom-0 left-1/2 -translate-x-1/2 translate-y-1/2";
  const Icon = horizontal
    ? expanded
      ? ChevronLeft
      : ChevronRight
    : expanded
      ? ChevronUp
      : ChevronDown;

  return (
    <button
      type="button"
      className={`nodrag nopan absolute z-10 inline-flex h-5 items-center gap-px rounded-full border border-border/50 bg-card px-1 text-xs tabular-nums leading-none text-muted-foreground ring-2 ring-card transition-colors hover:border-border hover:bg-muted hover:text-foreground ${position}`}
      title={label}
      aria-label={label}
      aria-expanded={expanded}
      onClick={(e) => {
        e.stopPropagation();
        onToggle?.();
      }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <Icon size={12} className="block shrink-0" aria-hidden />
      <span>{count}</span>
    </button>
  );
}
