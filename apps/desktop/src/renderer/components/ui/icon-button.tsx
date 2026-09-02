import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

export type IconButtonSize = "sm" | "md";
export type IconButtonTone =
  | "default"
  | "sidebar"
  | "primary"
  | "destructive"
  | "inverse";

const sizeClass: Record<IconButtonSize, string> = {
  sm: "size-7",
  md: "size-8",
};

const toneClass: Record<IconButtonTone, string> = {
  default: "text-muted-foreground hover:bg-accent hover:text-foreground",
  sidebar: "text-sidebar-foreground/60 hover:bg-sidebar-accent",
  primary:
    "bg-primary text-primary-foreground hover:bg-primary/90 hover:text-primary-foreground",
  destructive:
    "bg-destructive text-destructive-foreground hover:bg-destructive/90 hover:text-destructive-foreground",
  inverse:
    "bg-foreground text-background hover:bg-foreground/90 hover:text-background",
};

export interface IconButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement> {
  size?: IconButtonSize;
  /** Muted toolbar / sidebar chrome / filled primary·danger·inverse. */
  tone?: IconButtonTone;
}

/** Square icon-only button — sm = 28px, md = 32px per desktop-layout.mdc. */
export function IconButton({
  size = "sm",
  tone = "default",
  className,
  type = "button",
  ...props
}: IconButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-lg transition-colors duration-fast motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60",
        sizeClass[size],
        toneClass[tone],
        className,
      )}
      {...props}
    />
  );
}
