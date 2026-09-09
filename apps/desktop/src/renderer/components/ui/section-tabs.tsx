import { cn } from "@/lib/utils";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

export interface SectionTabItem {
  to: string;
  label: string;
  end?: boolean;
  icon?: ReactNode;
  badge?: ReactNode;
}

export interface SectionTabsProps {
  "aria-label": string;
  items: SectionTabItem[];
  /** Same-row trailing slot (toolbox: 市场). */
  action?: ReactNode;
  className?: string;
}

/**
 * Routed section switcher. Selected item is an accent capsule (nav selected,
 * not inverse). Not SegmentedControl (form, muted track) or TabChip (dock).
 */
export function SectionTabs({
  "aria-label": ariaLabel,
  items,
  action,
  className,
}: SectionTabsProps) {
  return (
    <nav
      aria-label={ariaLabel}
      className={cn(
        "flex items-center gap-2 border-b border-border pb-3",
        className,
      )}
    >
      {items.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.end}
          className={({ isActive }) =>
            cn(
              "inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-sm transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              isActive
                ? "bg-accent font-medium text-accent-foreground hover:bg-accent hover:text-accent-foreground"
                : "text-muted-foreground hover:text-foreground",
            )
          }
        >
          {tab.icon ? (
            <span aria-hidden="true" className="inline-flex">
              {tab.icon}
            </span>
          ) : null}
          {tab.label}
          {tab.badge}
        </NavLink>
      ))}
      {action ? (
        <div className="ml-auto flex shrink-0 items-center gap-3">{action}</div>
      ) : null}
    </nav>
  );
}
