import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant =
  | "primary"
  | "neutral"
  | "danger"
  | "destructive"
  | "ghost";
export type ButtonSize = "sm" | "md";

const variantClass: Record<ButtonVariant, string> = {
  primary: "bg-primary text-primary-foreground hover:bg-primary/90",
  neutral:
    "text-muted-foreground hover:bg-accent hover:text-foreground border border-transparent",
  danger: "text-destructive hover:bg-destructive/10",
  destructive:
    "bg-destructive text-destructive-foreground hover:bg-destructive/90",
  ghost: "text-foreground hover:bg-accent",
};

const sizeClass: Record<ButtonSize, string> = {
  sm: "h-7 gap-1 px-2.5 text-xs",
  md: "h-8 gap-1.5 px-3 text-xs",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Optional leading icon — sized for caption-level (14px) by default. */
  icon?: ReactNode;
}

/** Primary action control — maps to desktop-layout.mdc button tiers (sm h-7, md h-8). */
export function Button({
  variant = "primary",
  size = "sm",
  icon,
  className,
  children,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex items-center justify-center rounded-lg font-medium transition-colors duration-fast motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40",
        variantClass[variant],
        sizeClass[size],
        className,
      )}
      {...props}
    >
      {icon}
      {children}
    </button>
  );
}
