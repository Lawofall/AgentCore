import { SectionLabel } from "@/components/ui";
import type { ReactNode } from "react";

export const SHELF_TILE_CLASS = "w-[240px] shrink-0 snap-start";

/**
 * App Store-style collection row: title + optional See All, then a
 * horizontal snap scroller of fixed-width tiles.
 */
export function ShelfRail({
  title,
  action,
  children,
}: {
  title: string;
  action?: { label: string; onClick: () => void };
  children: ReactNode;
}) {
  return (
    <section className="mb-8">
      <div className="flex items-baseline justify-between gap-3">
        <SectionLabel>{title}</SectionLabel>
        {action ? (
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={action.onClick}
          >
            {action.label}
          </button>
        ) : null}
      </div>
      <div className="mt-3 flex snap-x snap-mandatory gap-3 overflow-x-auto pb-1 scrollbar-hidden">
        {children}
      </div>
    </section>
  );
}
