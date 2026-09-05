import { cn } from "@/lib/utils";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

export interface SectionTabItem {
  to: string;
  label: string;
  end?: boolean;
  badge?: ReactNode;
}

export interface SectionTabsProps {
  "aria-label": string;
  items: SectionTabItem[];
}

/**
 * In-page section switcher (underline). Not the dock's content tabs.
 */
export function SectionTabs({
  "aria-label": ariaLabel,
  items,
}: SectionTabsProps) {
  return (
    <nav
      aria-label={ariaLabel}
      className="flex items-center gap-4 border-b border-border"
    >
      {items.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.end}
          className={({ isActive }) =>
            cn(
              "relative inline-flex h-9 items-center gap-1.5 text-sm transition-colors",
              isActive
                ? "font-medium text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )
          }
        >
          {({ isActive }) => (
            <>
              {tab.label}
              {tab.badge}
              {isActive ? (
                <span
                  aria-hidden="true"
                  className="absolute inset-x-0 -bottom-px h-0.5 bg-primary"
                />
              ) : null}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
