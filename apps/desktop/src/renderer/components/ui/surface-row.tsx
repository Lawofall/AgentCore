import { cn } from "@/lib/utils";
import type {
  ButtonHTMLAttributes,
  CSSProperties,
  HTMLAttributes,
} from "react";
import { NavLink, type NavLinkProps } from "react-router-dom";

export type SurfaceRowVariant = "default" | "sidebar" | "file" | "settings";

export interface SurfaceRowProps extends HTMLAttributes<HTMLDivElement> {
  variant?: SurfaceRowVariant;
  active?: boolean;
  selected?: boolean;
  dropTarget?: boolean;
  cut?: boolean;
}

const variantClass: Record<SurfaceRowVariant, string> = {
  default:
    "flex items-center rounded-lg text-sm transition-colors hover:bg-accent",
  sidebar:
    "group flex h-9 w-full items-center gap-2 rounded-lg px-3 text-sm transition-colors text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground",
  file: "group flex items-center rounded-lg pr-1 text-xs max-md:text-sm hover:bg-accent",
  settings:
    "flex h-9 w-full items-center gap-3 rounded-lg px-3 text-sm transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground",
};

/** List row chrome — sidebar nav / conversations, file tree rows. The `sidebar`
 * variant's h-9 is the desktop-layout 导航项 exception; conversation-list rows
 * override it to h-8 so二级内容 doesn't carry nav-level height. */
export function SurfaceRow({
  variant = "default",
  active = false,
  selected = false,
  dropTarget = false,
  cut = false,
  className,
  ...props
}: SurfaceRowProps) {
  return (
    <div
      className={cn(
        variantClass[variant],
        variant === "sidebar" &&
          active &&
          "bg-sidebar-accent text-sidebar-accent-foreground",
        variant === "file" &&
          (active || selected) &&
          "bg-accent text-accent-foreground",
        variant === "settings" && active && "bg-accent text-accent-foreground",
        dropTarget && "bg-accent ring-1 ring-inset ring-primary",
        cut && "opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export interface SurfaceRowButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: SurfaceRowVariant;
  active?: boolean;
}

/** Sidebar-style clickable row (ConversationItem). */
export function SurfaceRowButton({
  variant = "sidebar",
  active = false,
  className,
  type = "button",
  ...props
}: SurfaceRowButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        variantClass[variant],
        active && "bg-sidebar-accent text-sidebar-accent-foreground",
        className,
      )}
      {...props}
    />
  );
}

/** Settings / More page nav row — NavLink wrapper. */
export function SurfaceNavLink({ className, ...props }: NavLinkProps) {
  return (
    <NavLink
      {...props}
      className={({ isActive, isPending, isTransitioning }) =>
        cn(
          variantClass.settings,
          isActive && "bg-accent text-accent-foreground",
          typeof className === "function"
            ? className({ isActive, isPending, isTransitioning })
            : className,
        )
      }
    />
  );
}

/** Indent helper for file tree depth. */
export function surfaceRowIndent(
  depth: number,
  indentBase = 0,
  step = 14,
  pad = 8,
): CSSProperties {
  return { paddingLeft: depth * step + pad + indentBase };
}
