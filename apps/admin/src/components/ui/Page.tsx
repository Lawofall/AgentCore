import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

/**
 * The single content container for every console page.
 *
 * Width, gutters and vertical rhythm live here rather than being re-typed per page —
 * nine hand-copied `max-w-[1200px]` wrappers (plus a 1400 and a `max-w-4xl`) used to
 * make the left edge jump as you moved between sections. The cap is generous because
 * this is a data console on a desktop browser: wide tables should use the screen.
 */
export function Page({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mx-auto w-full max-w-[1400px] px-6 py-6 lg:px-8",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * Page title block. `filters` is a separate row on purpose: cramming七个筛选控件
 * into the title row is what pushed the 用户 page header onto three lines at
 * narrow widths.
 */
export function PageHeader({
  title,
  description,
  note,
  actions,
  filters,
}: {
  title: ReactNode;
  description?: ReactNode;
  /** Small print under the description — caveats like the UTC window口径. */
  note?: ReactNode;
  actions?: ReactNode;
  filters?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-foreground">{title}</h1>
          {description && (
            <p className="mt-1 text-sm text-muted-foreground">{description}</p>
          )}
          {note && <p className="mt-1 text-xs text-muted-foreground">{note}</p>}
        </div>
        {actions && (
          <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
        )}
      </div>
      {filters && (
        <div className="flex flex-wrap items-center gap-2">{filters}</div>
      )}
    </header>
  );
}

/** Section heading inside a page — one step quieter than the page title. */
export function SectionHeader({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 border-border border-b px-5 py-3.5",
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {description && (
          <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/** Bordered surface used for every grouped block (metric card, table frame, form). */
export function Card({
  children,
  className,
  padded = false,
}: {
  children: ReactNode;
  className?: string;
  /** Adds the standard inner padding; leave off when the card wraps a table. */
  padded?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-card",
        padded && "p-5",
        className,
      )}
    >
      {children}
    </div>
  );
}
