"use client";

import { useEffect, useRef, useState } from "react";

/**
 * 轻量滚动入场：元素进入视口时加 `is-visible`，由 CSS 过渡接管。
 * 尊重 prefers-reduced-motion（CSS 侧已降级为直接显示）。
 *
 * `bare` 只挂 `is-visible`、不加默认的位移淡入——用于自身已有样式、
 * 只需要一个「进入视口」信号的元素（命题逐行点亮、示意图描边生长等）。
 */
export default function Reveal({
  children,
  delay = 0,
  className = "",
  bare = false,
  as: Tag = "div",
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
  bare?: boolean;
  as?: "div" | "p" | "li" | "span";
}) {
  const ref = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setVisible(true);
            observer.disconnect();
          }
        }
      },
      { threshold: 0.16, rootMargin: "0px 0px -10% 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <Tag
      ref={ref as React.Ref<never>}
      className={`${bare ? "" : "reveal"} ${visible ? "is-visible" : ""} ${className}`.trim()}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </Tag>
  );
}
