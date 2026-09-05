import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { cn } from "@/lib/utils";
import { ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export interface PageHeaderBack {
  to: string;
  label: string;
}

export interface PageHeaderProps {
  title: string;
  /** Same-row meta (date, count) — not a subtitle / lede. */
  meta?: ReactNode;
  /** Page-level action (新建 / 刷新). */
  action?: ReactNode;
  /** Hub-and-spoke back link (工具箱). */
  back?: PageHeaderBack;
  /**
   * Hairline under the header. Default: on when `back` is set (subpage),
   * off on hub / settings (settings already has a nav column).
   */
  bordered?: boolean;
  className?: string;
}

/**
 * One product page title: optional back, single-line h1, optional meta, right action.
 * Narrow settings already name the page in the back bar — hide the in-page h1 then.
 */
export function PageHeader({
  title,
  meta,
  action,
  back,
  bordered,
  className,
}: PageHeaderProps) {
  const { isNarrow } = useNarrowLayoutState();
  const showBorder = bordered ?? Boolean(back);
  const showTitle = Boolean(back) || !isNarrow;
  if (!showTitle && !meta && !action && !back) return null;

  return (
    <header
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-2",
        showBorder && "mb-6 border-b border-border pb-4",
        className,
      )}
    >
      {back ? (
        <>
          <Link
            to={back.to}
            className="inline-flex h-8 shrink-0 items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ChevronLeft size={16} />
            {back.label}
          </Link>
          <span aria-hidden="true" className="h-4 w-px shrink-0 bg-border" />
        </>
      ) : null}
      <div className="flex min-w-0 flex-1 items-baseline gap-3">
        {showTitle ? (
          <h1 className="min-w-0 text-xl font-semibold">{title}</h1>
        ) : null}
        {meta ? <p className="text-sm text-muted-foreground">{meta}</p> : null}
      </div>
      {action ? <div className="ml-auto shrink-0">{action}</div> : null}
    </header>
  );
}
