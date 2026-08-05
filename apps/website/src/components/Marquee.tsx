"use client";

/**
 * 无缝跑马灯。
 *
 * 轨道渲染两份相同内容、整体位移 100% 后瞬回——两份内容首尾相接，
 * 视觉上看不出接缝。第二份对读屏隐藏，避免内容被念两遍。
 * 悬停暂停、`prefers-reduced-motion` 下停住（见 globals.css）。
 */
export default function Marquee({
  items,
  duration = 38,
  gap = "4rem",
  reverse = false,
  className = "",
  renderItem,
}: {
  items: readonly string[];
  /** 跑完一整圈的秒数——越大越慢。 */
  duration?: number;
  gap?: string;
  reverse?: boolean;
  className?: string;
  renderItem: (item: string, index: number) => React.ReactNode;
}) {
  const track = (ariaHidden: boolean) => (
    <div className="marquee-track" aria-hidden={ariaHidden || undefined}>
      {items.map((item, i) => (
        <div key={`${item}-${i}`} className="flex shrink-0 items-center">
          {renderItem(item, i)}
        </div>
      ))}
    </div>
  );

  return (
    <div
      className={`marquee ${reverse ? "marquee-reverse" : ""} ${className}`.trim()}
      style={
        {
          "--duration": `${duration}s`,
          "--gap": gap,
        } as React.CSSProperties
      }
    >
      {track(false)}
      {track(true)}
    </div>
  );
}
