import type { LucideProps } from "lucide-react";
import { forwardRef } from "react";

/**
 * Ask identity: a question mark in the same 24×24 / stroke-2 box as ToolLine glyphs.
 * Not HelpCircle — the ring reads as a status badge.
 */
export const QuestionMark = forwardRef<SVGSVGElement, LucideProps>(
  function QuestionMark(
    {
      color = "currentColor",
      size = 14,
      strokeWidth = 2,
      absoluteStrokeWidth,
      className,
      ...props
    },
    ref,
  ) {
    const sw = absoluteStrokeWidth
      ? (Number(strokeWidth) * 24) / Number(size)
      : strokeWidth;
    return (
      <svg
        ref={ref}
        xmlns="http://www.w3.org/2000/svg"
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke={color}
        strokeWidth={sw}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        aria-hidden
        {...props}
      >
        <title>问号</title>
        <path d="M8 8.5C8 6.2 9.8 4.5 12 4.5c2.4 0 4 1.6 4 3.8 0 1.7-1 2.6-2.5 3.4-.9.5-1.5 1.2-1.5 2.3" />
        <path d="M12 18.5h.01" />
      </svg>
    );
  },
);
