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
const MARK_X = 164;
const MARK_Y = 244;
const MARK_W = 696;
const MARK_H = 516;

const PATH_G_AND_A =
  "M603.4 244.0 H402.4 C312.4 244.0 226.3 320.0 207.2 410.0 L168.1 594.0 C148.9 684.0 202.8 760.0 292.8 760.0 H342.8 C392.8 760.0 448.2 664.0 479.5 620.0 L747.4 244.0 H643.4 L418.3 560.0 C336.8 708.0 239.8 708.0 264.9 590.0 L301.0 420.0 C312.5 366.0 355.6 328.0 406.6 328.0 H543.6 Z";
const PATH_A_RIGHT = "M643.4 244.0 H747.4 L859.8 760.0 H755.8 Z";
const PATH_BAR = "M353.8 520.0 L763.8 520.0 L746.8 600.0 L336.8 600.0 Z";

/**
 * Shared product mark + GAgent wordmark.
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
        title={showWordmark ? undefined : "GAgent"}
      />
      {showWordmark && (
        <span className={`font-brand ${WORD_CLASS[size]}`}>GAgent</span>
      )}
    </div>
  );
}

/** GA ligature. `size` is the cap height; fill follows the surrounding text color. */
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
      <path d={PATH_G_AND_A} />
      <path d={PATH_A_RIGHT} />
      <path d={PATH_BAR} />
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
