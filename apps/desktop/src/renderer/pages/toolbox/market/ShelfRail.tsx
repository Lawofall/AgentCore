import { ASSEMBLY_CARD_GRID_CLASS, SectionLabel } from "@/components/ui";
import type { ReactNode } from "react";

/**
 * Catalog collection: title + the shared wrapping grid
 * (same tiles as 提示词). Not a horizontal scroller.
 */
export function ShelfRail({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="mb-8">
      <SectionLabel>{title}</SectionLabel>
      <div className={`mt-3 ${ASSEMBLY_CARD_GRID_CLASS}`}>{children}</div>
    </section>
  );
}
