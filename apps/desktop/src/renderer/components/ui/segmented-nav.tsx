import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";
import { NavLink } from "react-router-dom";

/** Badge counts above this render as `99+` so a segment can't stretch. */
const MAX_BADGE = 99;

export interface SegmentedNavItem {
  id: string;
  label: string;
  /** Router path; prefer an `APP_PATHS` constant over a literal. */
  to: string;
  /** Exact-match highlight. Default false keeps the segment lit on sub-routes. */
  end?: boolean;
  icon?: LucideIcon;
  /** Icon tint as a CSS `var(--token)` string — see `@/lib/catalogColors`. */
  colorVar?: string;
  /** Count chip; omitted or `<= 0` renders nothing. */
  badge?: number;
  /** Accessible name for the count chip, e.g. `3 条待处理`. */
  badgeLabel?: string;
}

export interface SegmentedNavProps {
  items: readonly SegmentedNavItem[];
  className?: string;
  "aria-label"?: string;
}

/**
 * Pill-style segmented navigation — one NavLink per segment, each with an
 * optional identity-colored icon and count badge. Active state comes from the
 * router, so segments deep-link and stay lit across their sub-routes.
 */
export function SegmentedNav({
  items,
  className,
  "aria-label": ariaLabel = "分区",
}: SegmentedNavProps) {
  return (
    <nav
      aria-label={ariaLabel}
      className={cn(
        // min-w-0 + 自滚：与返回链接、页级动作同处一行时，窄窗口下收缩自己而不是
        // 把整行撑出横向滚动条。`scrollbar-hidden`（globals.css，必须是普通类——
        // Tailwind 等价物会输给全局规则）藏掉 overlay 条，免得吃掉行高。
        "scrollbar-hidden flex w-fit min-w-0 items-center gap-0.5 overflow-x-auto rounded-lg border border-border p-0.5",
        className,
      )}
    >
      {items.map((item) => {
        const Icon = item.icon;
        const badge = item.badge ?? 0;
        return (
          <NavLink
            key={item.id}
            to={item.to}
            end={item.end ?? false}
            className={({ isActive }) =>
              cn(
                "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-3 text-sm transition-colors",
                isActive
                  ? "bg-accent text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )
            }
          >
            {Icon ? (
              <span
                className="flex shrink-0 items-center"
                style={item.colorVar ? { color: item.colorVar } : undefined}
              >
                <Icon size={14} />
              </span>
            ) : null}
            {item.label}
            {badge > 0 ? (
              <span
                aria-label={item.badgeLabel}
                className="flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1 text-xs font-medium text-primary-foreground"
              >
                {badge > MAX_BADGE ? `${MAX_BADGE}+` : badge}
              </span>
            ) : null}
          </NavLink>
        );
      })}
    </nav>
  );
}
