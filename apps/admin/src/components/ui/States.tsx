import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui/Spinner";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/** Nothing to show — a stated fact, not a failure. */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 px-6 py-14 text-center",
        className,
      )}
    >
      {Icon && (
        <div className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Icon size={18} />
        </div>
      )}
      <p className="text-sm text-muted-foreground">{title}</p>
      {description && (
        <p className="max-w-md text-xs text-muted-foreground">{description}</p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** A request failed and the operator can do something about it. */
export function ErrorState({
  message,
  onRetry,
  className,
}: {
  message: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card px-6 py-14 text-center text-sm",
        className,
      )}
    >
      <span className="text-destructive">{message}</span>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}

/** First-paint placeholder that reserves the table's shape instead of collapsing it. */
export function TableSkeleton({
  rows = 6,
  columns,
  className,
}: {
  rows?: number;
  columns: number;
  className?: string;
}) {
  return (
    <div
      className={cn("rounded-xl border border-border bg-card p-4", className)}
      aria-hidden
    >
      <div className="flex flex-col gap-3">
        {Array.from({ length: rows }, (_, r) => (
          <div key={`row-${r}`} className="flex items-center gap-4">
            {Array.from({ length: columns }, (_, c) => (
              <div
                key={`cell-${r}-${c}`}
                className="h-4 flex-1 animate-pulse rounded bg-muted"
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Keeps already-rendered content on screen while it refreshes, dimmed and inert.
 *
 * Swapping the table + pager out for a centered spinner (what 对话 / 审计 used to do)
 * collapses the page to nothing and then re-expands it on every filter flip, throwing
 * away the scroll position each time.
 */
export function Refreshing({
  active,
  children,
  className,
}: {
  active: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      aria-busy={active || undefined}
      className={cn(
        "transition-opacity",
        active && "pointer-events-none opacity-60",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * A refresh failed on top of data that is already on screen.
 *
 * Keeping the stale snapshot and labelling it beats collapsing a working page into an
 * error state — the numbers are still worth something, they are just from a minute
 * ago. `role="status"` because this appears *after* the page settled: without it the
 * banner is silent to a screen reader and the data silently becomes a lie.
 */
export function StaleDataNotice({
  message,
  onRetry,
  className,
}: {
  message: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="status"
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-5 py-3.5 text-sm",
        className,
      )}
    >
      <span className="text-destructive">刷新失败，以下为上一次的数据：{message}</span>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}

/** Inline "working…" marker for headers and toolbars. */
export function InlineBusy({ label = "加载中…" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-muted-foreground text-sm">
      <Spinner />
      {label}
    </span>
  );
}
