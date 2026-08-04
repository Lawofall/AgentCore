/**
 * 品牌标记：一个方框内三个不等亮度的小方块——
 * 主色 / 副色 / 中性各一，对应「一支多元的团队」而非单个节点。
 *
 * `onLight` 用于白底（悬浮胶囊导航、白色页脚）：第三块由「白的 35%」
 * 换成「黑的 30%」，否则在白纸上直接消失。
 */
export default function BrandMark({
  size = 22,
  onLight = false,
}: {
  size?: number;
  onLight?: boolean;
}) {
  const unit = size / 22;
  const box = 5 * unit;
  const inset = 4 * unit;

  return (
    <span
      aria-hidden="true"
      className="relative block shrink-0"
      style={{ width: size, height: size }}
    >
      <span
        className="absolute inset-0 rounded-[0.23em] border border-primary"
        style={{ borderRadius: 5 * unit }}
      />
      <span
        className="absolute block rounded-[1px] bg-primary"
        style={{ left: inset, top: inset, width: box, height: box }}
      />
      <span
        className="absolute block rounded-[1px] bg-brand-2/75"
        style={{ right: inset, top: inset, width: box, height: box }}
      />
      <span
        className={`absolute block rounded-[1px] ${
          onLight ? "bg-paper-ink/30" : "bg-foreground/35"
        }`}
        style={{ left: 8.5 * unit, bottom: inset, width: box, height: box }}
      />
    </span>
  );
}
