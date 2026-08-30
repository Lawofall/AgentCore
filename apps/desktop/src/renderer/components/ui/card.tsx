import { cn } from "@/lib/utils";
import type { HTMLAttributes } from "react";

export type CardVariant = "default" | "muted" | "interactive";

/** Exported so non-`div` surfaces that must read as a card (e.g. a clickable
 *  settings row rendered as a `<button>`) reuse the same chrome instead of
 *  re-typing an equivalent class string. */
export const cardVariantClass: Record<CardVariant, string> = {
  default: "border-border bg-card",
  muted: "border-border bg-card/60",
  interactive:
    "border-border bg-card transition-colors hover:border-primary/40 hover:bg-accent/40",
};

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant;
}

/** Surface container — large radius (rounded-xl) per desktop-layout.mdc. */
export function Card({ variant = "default", className, ...props }: CardProps) {
  return (
    <div
      className={cn("rounded-xl border", cardVariantClass[variant], className)}
      {...props}
    />
  );
}

export function CardHeader({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex items-start gap-2 px-3 pt-3", className)}
      {...props}
    />
  );
}

export function CardBody({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-3", className)} {...props} />;
}

export function CardFooter({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-end gap-2 border-t px-3 py-2.5",
        className,
      )}
      {...props}
    />
  );
}
