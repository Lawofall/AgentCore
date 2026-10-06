import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface SegmentedControlItem<T extends string = string> {
  value: T;
  label: string;
  id?: string;
  "aria-controls"?: string;
}

export interface SegmentedControlProps<T extends string = string> {
  "aria-label": string;
  value: T;
  onChange: (value: T) => void;
  items: readonly SegmentedControlItem<T>[];
  /** Keep every segment on one line. Use when the option count fits the column. */
  fit?: boolean;
  className?: string;
}

/**
 * In-place mutually exclusive capsule switch (login↔register).
 * Selected segment lifts as a card. Not SectionTabs (routed accent capsule) or TabChip (dock).
 */
export function SegmentedControl<T extends string>({
  "aria-label": ariaLabel,
  value,
  onChange,
  items,
  fit = false,
  className,
}: SegmentedControlProps<T>) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        "scrollbar-hidden flex min-w-0 gap-1 rounded-lg bg-muted p-1",
        fit ? "overflow-hidden" : "overflow-x-auto",
        className,
      )}
    >
      {items.map((item) => {
        const selected = item.value === value;
        return (
          <Button
            key={item.value}
            variant="ghost"
            size="md"
            role="tab"
            id={item.id}
            aria-selected={selected}
            aria-controls={item["aria-controls"]}
            onClick={() => onChange(item.value)}
            className={cn(
              "h-8 min-w-0 flex-1 rounded-lg px-3 text-sm",
              fit ? "shrink truncate" : "shrink-0 whitespace-nowrap",
              selected
                ? "bg-card text-foreground shadow-raised hover:bg-card"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {item.label}
          </Button>
        );
      })}
    </div>
  );
}
