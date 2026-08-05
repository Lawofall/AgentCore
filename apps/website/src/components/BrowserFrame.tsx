/**
 * 浏览器窗口外框。
 *
 * Hero 右侧那块「产品截图」的容器：mac 三点 + 地址栏 + 内容区，
 * 整体沿 X 轴轻微后仰（perspective + rotateY），背后压一层渐变辉光，
 * 让它看起来是浮在星野里的一块屏幕，而不是贴在页面上的一张图。
 */
export default function BrowserFrame({
  url,
  children,
  tilt = true,
}: {
  url: string;
  children: React.ReactNode;
  tilt?: boolean;
}) {
  return (
    <div className="relative" style={{ perspective: "1600px" }}>
      {/* 背后的渐变辉光 */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -inset-x-10 -inset-y-8 rounded-[2rem] opacity-60 blur-3xl"
        style={{ background: "var(--brand-gradient)" }}
      />

      <div
        className={`relative overflow-hidden rounded-[0.875rem] border border-white/10 bg-[color-mix(in_oklab,var(--ink-deep),black_20%)] shadow-[0_40px_90px_-30px_rgba(0,0,0,0.9)] ${
          tilt ? "frame-tilt" : ""
        }`}
      >
        {/* 窗口栏 */}
        <div className="flex items-center gap-3 border-b border-white/[0.07] px-3.5 py-2.5">
          <div className="flex shrink-0 gap-[6px]">
            <span className="size-[9px] rounded-full bg-[#ff5f57]" />
            <span className="size-[9px] rounded-full bg-[#febc2e]" />
            <span className="size-[9px] rounded-full bg-[#28c840]" />
          </div>
          <div className="flex min-w-0 flex-1 items-center justify-center gap-1.5 rounded-md bg-white/[0.06] px-3 py-1">
            <svg
              aria-hidden="true"
              viewBox="0 0 12 12"
              className="size-[9px] shrink-0 fill-none stroke-white/40"
              strokeWidth="1.2"
            >
              <rect x="2.5" y="5.2" width="7" height="5" rx="1.2" />
              <path d="M4.2 5.2V3.8a1.8 1.8 0 0 1 3.6 0v1.4" />
            </svg>
            <span className="truncate font-mono text-[0.625rem] tracking-[0.02em] text-white/45">
              {url}
            </span>
          </div>
          <div className="w-[42px] shrink-0" />
        </div>

        {/* 内容区 */}
        <div className="relative">{children}</div>
      </div>
    </div>
  );
}
