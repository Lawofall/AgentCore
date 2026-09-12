import { CATALOG_GRID_CLASS, SectionLabel } from "@/components/ui";
import type { ReactNode } from "react";

/**
 * Discover collection: title + optional See All, then the shared wrapping
 * catalog grid (same tiles as 提示词 / 创作). Not a horizontal scroller.
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
      <div className={`mt-3 ${CATALOG_GRID_CLASS}`}>{children}</div>
    </section>
  );
}
