import { cn } from "@/lib/utils";
import {
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type ReactNode,
  type Ref,
  forwardRef,
} from "react";
import { type StatusTone, statusChip } from "./tone-presets";

export type BadgeTone = StatusTone;

type BadgeShared = {
  tone?: BadgeTone;
  /** Pill (rounded-full) vs inline chip (rounded-lg). */
  pill?: boolean;
  children?: ReactNode;
  className?: string;
};

export type BadgeProps =
  | ({ as?: "span" } & HTMLAttributes<HTMLSpanElement> & BadgeShared)
  | ({ as: "button" } & ButtonHTMLAttributes<HTMLButtonElement> & BadgeShared);

/** Status / count / role chip — semantic tones only (color-tokens.mdc). */
export const Badge = forwardRef<
  HTMLSpanElement | HTMLButtonElement,
  BadgeProps
>(function Badge(
  { tone = "muted", pill = false, as = "span", className, ...props },
  ref,
) {
  const classes = cn(
    "inline-flex shrink-0 items-center border px-1.5 py-0.5 text-xs leading-none",
    pill ? "rounded-full" : "rounded-lg",
    statusChip[tone],
    as === "button" &&
      "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    className,
  );
  if (as === "button") {
    const buttonProps = props as ButtonHTMLAttributes<HTMLButtonElement>;
    return (
      <button
        {...buttonProps}
        ref={ref as Ref<HTMLButtonElement>}
        type="button"
        className={classes}
      />
    );
  }
  return (
    <span
      {...(props as HTMLAttributes<HTMLSpanElement>)}
      ref={ref as Ref<HTMLSpanElement>}
      className={classes}
    />
  );
});
