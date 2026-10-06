import type { HTMLAttributes } from "react";

export type BrandMarkSize = "sm" | "md" | "lg";
export type BrandMarkLayout = "inline" | "stack";

const MARK_PX: Record<BrandMarkSize, number> = {
  sm: 18,
  md: 28,
  lg: 36,
};

const WORD_CLASS: Record<BrandMarkSize, string> = {
  sm: "text-sm font-semibold tracking-tight",
  md: "text-xl font-semibold tracking-tight",
  lg: "text-xl font-semibold tracking-tight",
};

/** Ligature bounds inside assets/agentcore-icon.svg (1024 canvas). */
const MARK_X = 150;
const MARK_Y = 245;
const MARK_W = 727;
const MARK_H = 516;

const PATH_A_AND_LOWER_C =
  "M 412,245 L 266,529 L 150,761 L 250,761 L 303,647 L 313,630 L 498,630 L 477,558 L 349,558 L 347,556 L 421,408 L 451,408 L 518,642 L 538,680 L 562,709 L 591,732 L 615,745 L 642,755 L 664,760 L 823,761 L 832,677 L 703,677 L 685,674 L 667,668 L 648,658 L 631,645 L 616,628 L 604,608 L 595,586 L 494,246 Z";
const PATH_UPPER_C =
  "M 877,245 L 729,245 L 699,249 L 667,258 L 636,272 L 610,289 L 586,311 L 564,338 L 547,369 L 582,486 L 598,486 L 602,450 L 607,423 L 617,401 L 640,369 L 654,356 L 671,345 L 695,334 L 714,329 L 732,327 L 868,327 Z";

/**
 * Shared product mark + AgentCore wordmark.
 * Display font (`font-brand`) applies to the Latin wordmark only; CJK copy nearby stays on the system stack.
 */
export function BrandMark({
  size = "md",
  layout = "inline",
  showWordmark = true,
  className = "",
  ...rest
}: {
  size?: BrandMarkSize;
  layout?: BrandMarkLayout;
  showWordmark?: boolean;
  className?: string;
} & HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`inline-flex items-center ${
        layout === "stack" ? "flex-col gap-2" : "gap-2"
      } ${className}`}
      {...rest}
    >
      <BrandMarkIcon
        size={MARK_PX[size]}
        title={showWordmark ? undefined : "AgentCore"}
      />
      {showWordmark && (
        <span className={`font-brand ${WORD_CLASS[size]}`}>AgentCore</span>
      )}
    </div>
  );
}

/** AC ligature. `size` is the cap height; fill follows the surrounding text color. */
export function BrandMarkIcon({
  size = 24,
  className = "",
  title,
}: {
  size?: number;
  className?: string;
  /** Accessible name when shown alone; omit beside a visible wordmark. */
  title?: string;
}) {
  const width = Math.round((size * MARK_W) / MARK_H);
  const mark = (
    <>
      <path d={PATH_A_AND_LOWER_C} />
      <path d={PATH_UPPER_C} />
    </>
  );
  const svgClass = `shrink-0 ${className}`;

  if (!title) {
    return (
      // biome-ignore lint/a11y/noSvgWithoutTitle: decorative companion mark; visible wordmark provides the name
      <svg
        width={width}
        height={size}
        viewBox={`${MARK_X} ${MARK_Y} ${MARK_W} ${MARK_H}`}
        fill="currentColor"
        xmlns="http://www.w3.org/2000/svg"
        className={svgClass}
        aria-hidden
      >
        {mark}
      </svg>
    );
  }

  return (
    <svg
      width={width}
      height={size}
      viewBox={`${MARK_X} ${MARK_Y} ${MARK_W} ${MARK_H}`}
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
      className={svgClass}
      role="img"
      aria-label={title}
    >
      <title>{title}</title>
      {mark}
    </svg>
  );
}
