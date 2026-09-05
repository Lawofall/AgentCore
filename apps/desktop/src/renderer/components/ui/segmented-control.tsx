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
  className?: string;
}

/**
 * In-place mutually exclusive capsule switch (login↔register, role identity).
 * Selected segment lifts as a card. Not SectionTabs (routed underline) or TabChip (dock).
 */
export function SegmentedControl<T extends string>({
  "aria-label": ariaLabel,
  value,
  onChange,
  items,
  className,
}: SegmentedControlProps<T>) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        "scrollbar-hidden flex min-w-0 gap-1 overflow-x-auto rounded-lg bg-muted p-1",
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
              "h-8 min-w-0 flex-1 shrink-0 whitespace-nowrap rounded-lg px-3 text-sm",
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
