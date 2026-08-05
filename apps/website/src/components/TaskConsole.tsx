"use client";

import { useLang } from "@/components/LangProvider";
import { CONSOLE } from "@/content/home";

const ACCENT: Record<"primary" | "brand-2", string> = {
  primary: "var(--primary)",
  "brand-2": "var(--brand-2)",
};

/** 日志与进度条共用的循环周期，须与 globals.css 中的 8.4s 保持一致。 */
const STAGGER = 0.42;

/**
 * Hero 里的「一次真实任务」控制台。
 *
 * 纯装饰：日志与进度条是 CSS 循环动画，不接任何实时数据；
 * 因此整块对辅助技术隐藏，文案信息在下方各分区都有等价表达。
 */
export default function TaskConsole({
  compact = false,
  bare = false,
}: {
  compact?: boolean;
  /** 外框由 <BrowserFrame> 提供时开启：去掉自带的圆角描边与 mac 三点。 */
  bare?: boolean;
}) {
  const { t } = useLang();
  const logs = compact ? CONSOLE.logs.filter((_, i) => i !== 3) : CONSOLE.logs;
  const bars = compact ? CONSOLE.bars.filter((_, i) => i !== 1) : CONSOLE.bars;

  return (
    <div
      aria-hidden="true"
      className={
        bare
          ? "bg-[linear-gradient(180deg,color-mix(in_oklab,var(--ink-deep),white_4%),var(--ink-deep))]"
          : "overflow-hidden rounded-2xl border border-border-strong bg-[linear-gradient(180deg,var(--card),color-mix(in_oklab,var(--background),var(--card)_45%))] shadow-[0_40px_90px_-40px_oklch(0_0_0/0.9)]"
      }
    >
      {/* 标题栏 */}
      <div className="flex items-center gap-3 border-b border-border-soft px-4 py-3">
        {!compact && !bare && (
          <span className="flex gap-1.5">
            <span className="block size-2 rounded-full bg-secondary" />
            <span className="block size-2 rounded-full bg-secondary" />
            <span className="block size-2 rounded-full bg-secondary" />
          </span>
        )}
        <span className="font-mono text-[0.65625rem] text-ghost">
          {compact ? CONSOLE.taskIdShort : CONSOLE.taskId}
        </span>
        <span className="ml-auto flex items-center gap-[7px] font-mono text-[0.625rem] tracking-[0.14em] text-primary">
          <span className="console-pulse block size-1.5 rounded-full bg-primary" />
          {CONSOLE.status}
        </span>
      </div>

      {/* 日志区 */}
      <div
        className={`flex flex-col gap-3 px-[0.875rem] py-4 sm:gap-[0.6875rem] sm:px-[1.125rem] sm:py-5 ${
          compact ? "min-h-[12.25rem]" : "min-h-[14.875rem]"
        }`}
      >
        {logs.map((log, i) => (
          <div
            key={log.time}
            className="console-log flex items-baseline gap-[9px] sm:gap-3"
            style={{ animationDelay: `${0.6 + i * STAGGER}s` }}
          >
            {!compact && (
              <span className="shrink-0 font-mono text-[0.65625rem] text-[var(--ghost)] opacity-70">
                {log.time}
              </span>
            )}
            <span
              className="shrink-0 basis-[2.625rem] font-mono text-[0.625rem] sm:basis-[3.375rem] sm:text-[0.65625rem]"
              style={{ color: ACCENT[log.accent] }}
            >
              {t(log.actor)}
            </span>
            <span
              className={`text-[0.8125rem] leading-[1.55] ${
                log.strong ? "text-foreground/90" : "text-muted-foreground"
              }`}
            >
              {t(log.text)}
              {log.caret && (
                <span className="console-caret ml-1.5 inline-block h-[0.875rem] w-[7px] translate-y-[2px] bg-primary align-baseline" />
              )}
            </span>
          </div>
        ))}
      </div>

      {/* 进度区 */}
      <div
        className={`grid gap-x-[1.375rem] gap-y-3 border-t border-border-soft bg-foreground/[0.012] px-[0.875rem] py-[0.8125rem] sm:px-[1.125rem] sm:py-[0.9375rem] ${
          compact ? "grid-cols-1" : "grid-cols-1 sm:grid-cols-2"
        }`}
      >
        {bars.map((bar, i) => (
          <div key={bar.id} className="flex items-center gap-2.5">
            {!compact && (
              <span className="shrink-0 basis-[1.625rem] font-mono text-[0.65625rem] text-ghost">
                {bar.id}
              </span>
            )}
            <span className="shrink-0 basis-[2.625rem] text-xs text-muted-foreground">
              {t(bar.label)}
            </span>
            <span className="relative h-0.5 flex-1 overflow-hidden rounded-sm bg-foreground/[0.08]">
              <span
                className="console-bar absolute inset-0 block"
                style={
                  {
                    background: ACCENT[bar.accent],
                    "--bar-scale": bar.value,
                    animationDelay: `${1 + i * STAGGER}s`,
                  } as React.CSSProperties
                }
              />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
