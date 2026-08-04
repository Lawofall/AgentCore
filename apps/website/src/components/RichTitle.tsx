import type { JSX } from "react";

/**
 * 把带行内标记的标题文案渲染成 JSX。
 *
 *   [[词]]  → 衬线斜体 + 渐变色（中文由 CSS 回落宋体、取消倾斜）
 *   {{词}}  → 手绘波浪下划线
 *   \n      → 强制换行（每行一个 block，方便逐行控制行高）
 *
 * 标记写在 content/home.ts 的文案里，因为强调词在中英文里落在句子的
 * 不同位置——拆成 pre/accent/post 三段会逼着译文迁就结构。
 */
const TOKEN = /(\[\[[^\]]+\]\]|\{\{[^}]+\}\})/g;

function renderLine(line: string, keyPrefix: string) {
  return line.split(TOKEN).map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if (part.startsWith("[[") && part.endsWith("]]")) {
      return (
        <em key={key} className="accent grad-text">
          {part.slice(2, -2)}
        </em>
      );
    }
    if (part.startsWith("{{") && part.endsWith("}}")) {
      return (
        <span key={key} className="squiggle">
          {part.slice(2, -2)}
        </span>
      );
    }
    return <span key={key}>{part}</span>;
  });
}

export default function RichTitle({
  text,
  as: Tag = "h2",
  className = "",
  style,
}: {
  text: string;
  as?: keyof JSX.IntrinsicElements;
  className?: string;
  /** 主要给 float-in 的 animationDelay 用。 */
  style?: React.CSSProperties;
}) {
  const lines = text.split("\n");

  return (
    // @ts-expect-error —— 动态标签名，React 的 JSX 类型推不出这里的 props 交集。
    <Tag className={className} style={style}>
      {lines.map((line, i) => (
        <span key={`line-${i}`} className="block">
          {renderLine(line, `l${i}`)}
        </span>
      ))}
    </Tag>
  );
}
