/**
 * 「问题」区的一对示意图。
 *
 * 左：单 Agent —— 一条首尾相接的直链，节点全是空心，没有回路。
 * 右：AgentCore —— 从一个入口分叉、并行、交叉互审后收敛回一个出口。
 *
 * 两者都靠父级 `.is-visible`（Reveal 提供）触发描边生长；
 * 路径用 pathLength 归一化，无需在运行时量 getTotalLength。
 */

const LEN = 100;

/** 归一化后的描边长度，供 CSS 的 stroke-dasharray 使用。 */
const dash = { "--len": LEN } as React.CSSProperties;

export function ChainDiagram() {
  // 直链需要「点—线—点…」交替的扁平子节点：CSS 的 nth-child 依次给出入场延迟。
  return (
    <div className="mb-[1.875rem] flex h-14 items-center">
      {Array.from({ length: 9 }, (_, i) =>
        i % 2 === 0 ? (
          <span
            key={i}
            className="chain-part block size-3 shrink-0 rounded-full border border-line"
          />
        ) : (
          <span key={i} className="chain-part block h-px flex-1 bg-line/70" />
        ),
      )}
    </div>
  );
}

export function BranchesDiagram() {
  return (
    <div className="relative mb-[1.875rem] h-14">
      <svg
        viewBox="0 0 420 56"
        preserveAspectRatio="none"
        aria-hidden="true"
        className="absolute inset-0 size-full overflow-visible"
      >
        {/* 分叉 → 并行 → 交叉互审（副色）→ 收敛 */}
        {[
          { d: "M22 28 C70 28 70 8 118 8", accent: false },
          { d: "M22 28 C70 28 70 48 118 48", accent: false },
          { d: "M118 8 L266 8", accent: false },
          { d: "M118 48 L266 48", accent: false },
          { d: "M118 8 C190 8 194 48 266 48", accent: true },
          { d: "M266 8 C330 8 340 28 398 28", accent: false },
          { d: "M266 48 C330 48 340 28 398 28", accent: false },
        ].map((p, i) => (
          <path
            key={p.d}
            className="branch-path"
            d={p.d}
            pathLength={LEN}
            fill="none"
            stroke={p.accent ? "var(--brand-2)" : "var(--primary)"}
            strokeWidth={1}
            opacity={p.accent ? 0.4 : 0.55}
            style={{ ...dash, transitionDelay: `${i * 80}ms` }}
          />
        ))}
      </svg>

      {[
        { className: "left-4 top-[22px] bg-primary" },
        { className: "left-[112px] top-0.5 bg-primary/85" },
        { className: "left-[112px] top-[42px] bg-primary/85" },
        { className: "left-[260px] top-0.5 bg-brand-2/85" },
        { className: "left-[260px] top-[42px] bg-brand-2/85" },
        { className: "right-4 top-[22px] bg-primary" },
      ].map((dot) => (
        <span
          key={dot.className}
          className={`chain-part absolute block size-3 rounded-full ${dot.className}`}
        />
      ))}
    </div>
  );
}
