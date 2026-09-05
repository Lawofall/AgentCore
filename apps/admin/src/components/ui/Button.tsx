import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "outline" | "ghost" | "destructive";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40",
  outline:
    "border border-border bg-card text-foreground hover:bg-accent disabled:opacity-40",
  ghost: "text-foreground hover:bg-accent disabled:opacity-40",
  destructive:
    "bg-destructive text-destructive-foreground hover:opacity-90 disabled:opacity-40",
};

const SIZES: Record<Size, string> = {
  sm: "h-7 gap-1 px-2.5 text-xs",
  md: "h-8 gap-1.5 px-3 text-xs",
};

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export function Button({
  variant = "primary",
  size = "sm",
  className,
  type = "button",
  ...props
}: Props) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex shrink-0 items-center justify-center gap-1 rounded-lg font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    />
  );
}
