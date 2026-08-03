"use client";

import { useEffect, useRef, useState } from "react";
import { useLang } from "@/components/LangProvider";
import { MECHANISM } from "@/content/home";

/* ── 图形常量 ─────────────────────────────────────────────── */

const VIEW_W = 960;
const VIEW_H = 460;
const LEN = 100;

/** 连线：id → 路径。l9 是正/反两方的辩论线，用副色区分。 */
const LINKS: { id: string; d: string; debate?: boolean }[] = [
  { id: "l1", d: "M120 230 L155 230" },
  { id: "l2", d: "M275 230 C302 230 300 100 327 100" },
  { id: "l3", d: "M275 230 C302 230 300 360 327 360" },
  { id: "l4", d: "M453 100 L502 100" },
  { id: "l5", d: "M453 360 L502 360" },
  { id: "l6", d: "M453 100 C482 100 473 360 502 360" },
  { id: "l7", d: "M628 100 L685 100" },
  { id: "l8", d: "M628 360 L685 360" },
  { id: "l9", d: "M740 122 L740 338", debate: true },
  { id: "l10", d: "M795 100 C814 100 810 230 827 230" },
  { id: "l11", d: "M795 360 C814 360 810 230 827 230" },
  { id: "l12", d: "M890 208 C890 60 886 28 820 28 L140 28 C74 28 70 60 70 208" },
];

/** 节点：left/top 为占位百分比，w 为设计稿固定宽度（随画布等比缩放）。 */
const NODES: {
  id: keyof typeof MECHANISM.nodes;
  left: number;
  top: number;
  w: number;
  debate?: boolean;
}[] = [
  { id: "you", left: 7.29, top: 50, w: 100 },
  { id: "ceo", left: 22.4, top: 50, w: 120 },
  { id: "w1a", left: 40.6, top: 21.7, w: 126 },
  { id: "w1b", left: 40.6, top: 78.3, w: 126 },
  { id: "w2a", left: 58.9, top: 21.7, w: 126 },
  { id: "w2b", left: 58.9, top: 78.3, w: 126 },
  { id: "w3a", left: 77.1, top: 21.7, w: 110, debate: true },
  { id: "w3b", left: 77.1, top: 78.3, w: 110, debate: true },
  { id: "fin", left: 92.7, top: 50, w: 126 },
];

/**
 * 时间线提示点：`at` 为设计稿的时间轴刻度，DURATION 为总长。
 * 滚动进度映射到该刻度后，凡是 `at <= now` 的都视为已点亮。
 */
const DURATION = 6.1;
const CUES: { at: number; step?: number; nodes?: string[]; links?: string[] }[] =
  [
    { at: 0, step: 0 },
    { at: 0.1, nodes: ["you"] },
    { at: 0.4, links: ["l1"] },
    { at: 1.1, step: 1 },
    { at: 1.2, nodes: ["ceo"] },
    { at: 1.5, links: ["l2", "l3"] },
    { at: 2.3, step: 2 },
    { at: 2.4, nodes: ["w1a", "w1b"] },
    { at: 2.9, links: ["l4", "l5", "l6"] },
    { at: 3.1, nodes: ["w2a", "w2b"] },
    { at: 3.6, links: ["l7", "l8"] },
    { at: 3.8, nodes: ["w3a", "w3b"] },
    { at: 4.1, links: ["l9"] },
    { at: 4.7, step: 3 },
    { at: 4.8, links: ["l10", "l11"] },
    { at: 5.1, nodes: ["fin"] },
    { at: 5.5, links: ["l12"] },
  ];

function stateAt(progress: number) {
  const now = progress * DURATION;
  const nodes = new Set<string>();
  const links = new Set<string>();
  let step = 0;
  for (const cue of CUES) {
    if (cue.at > now) break;
    if (cue.step !== undefined) step = cue.step;
    cue.nodes?.forEach((n) => nodes.add(n));
    cue.links?.forEach((l) => links.add(l));
  }
  return { step, nodes, links };
}

/* ── 组件 ─────────────────────────────────────────────────── */

/**
 * 协作机制区：宽屏下钉住舞台，用滚动进度逐波点亮「作战图」；
 * 窄屏改用竖向时间轴（作战图缩到手机宽度后文字不可读，不做等比塞入）。
 */
export default function MechanismBoard() {
  const { t } = useLang();
  const wrapRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [progress, setProgress] = useState(0);
  const [scale, setScale] = useState(1);

  // 滚动进度：钉住区间内 0 → 1。
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const travel = wrap.offsetHeight - window.innerHeight;
        if (travel <= 0) return setProgress(1);
        const passed = -wrap.getBoundingClientRect().top;
        setProgress(Math.min(1, Math.max(0, passed / travel)));
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  // 画布等比缩放：内层固定 960×460，按容器宽度缩放，保持所有间距比例。
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const observer = new ResizeObserver(([entry]) => {
      setScale(entry.contentRect.width / VIEW_W);
    });
    observer.observe(box);
    return () => observer.disconnect();
  }, []);

  const { step, nodes, links } = stateAt(progress);

  return (
    <section id="how" className="relative z-[1] border-t border-border-soft">
      {/* ── 宽屏：钉住 + 滚动驱动 ── */}
      <div
        ref={wrapRef}
        className="hidden min-[70rem]:block"
        style={{ height: `calc(100vh + ${VIEW_H * 7.4}px)` }}
      >
        <div className="sticky top-0 flex h-screen min-h-[41.25rem] flex-col justify-center py-14">
          <div className="container-x">
            <header className="mb-[2.375rem] flex items-end justify-between gap-8">
              <div>
                <p className="eyebrow mb-4">{t(MECHANISM.eyebrow)}</p>
                <h2 className="m-0 text-[clamp(1.75rem,3.1vw,2.75rem)] font-semibold leading-[1.14] tracking-[-0.03em]">
                  {t(MECHANISM.title)}
                </h2>
              </div>
              <p className="whitespace-nowrap pb-1.5 font-mono text-[0.6875rem] tracking-[0.16em] text-ghost">
                <span className="text-primary">
                  {MECHANISM.steps[step].idx}
                </span>{" "}
                / 04
              </p>
            </header>

            <div className="grid grid-cols-[18.625rem_minmax(0,1fr)] items-center gap-11">
              <ol className="m-0 flex list-none flex-col gap-1 p-0">
                {MECHANISM.steps.map((s, i) => (
                  <li
                    key={s.idx}
                    className={`step-item ${i === step ? "is-active" : ""}`}
                  >
                    <div className="mb-[7px] flex items-baseline gap-2.5">
                      <span className="step-idx font-mono text-[0.65625rem]">
                        {s.idx}
                      </span>
                      <span className="step-title text-base font-medium">
                        {t(s.title)}
                      </span>
                    </div>
                    <p className="step-desc m-0 text-[0.84375rem] leading-[1.7]">
                      {t(s.body)}
                    </p>
                  </li>
                ))}
              </ol>

              <div
                ref={boxRef}
                className="relative w-full"
                style={{ aspectRatio: `${VIEW_W} / ${VIEW_H}` }}
              >
                <div
                  className="absolute left-0 top-0 origin-top-left"
                  style={{
                    width: VIEW_W,
                    height: VIEW_H,
                    transform: `scale(${scale})`,
                  }}
                >
                  <svg
                    viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
                    aria-hidden="true"
                    className="absolute inset-0 size-full overflow-visible"
                  >
                    {LINKS.map((l) => (
                      <path
                        key={l.id}
                        className={`board-link ${links.has(l.id) ? "is-on" : ""}`}
                        d={l.d}
                        pathLength={LEN}
                        style={{ "--len": LEN } as React.CSSProperties}
                      />
                    ))}
                    {LINKS.map((l) => (
                      <path
                        key={`flow-${l.id}`}
                        className={`board-flow ${links.has(l.id) ? "is-on" : ""}`}
                        d={l.d}
                        stroke={l.debate ? "var(--brand-2)" : "var(--primary)"}
                      />
                    ))}
                  </svg>

                  {NODES.map((n) => (
                    <div
                      key={n.id}
                      className={`board-node absolute ${nodes.has(n.id) ? "is-on" : ""}`}
                      style={
                        {
                          left: `${n.left}%`,
                          top: `${n.top}%`,
                          width: n.w,
                          marginLeft: -n.w / 2,
                          marginTop: -22,
                          "--node-accent": n.debate
                            ? "var(--brand-2)"
                            : "var(--primary)",
                        } as React.CSSProperties
                      }
                    >
                      <span className="board-dot" />
                      <span className="board-label">
                        {t(MECHANISM.nodes[n.id])}
                      </span>
                    </div>
                  ))}

                  <div className="absolute -bottom-[1.875rem] left-0 flex gap-[1.375rem] font-mono text-[0.625rem] tracking-[0.13em] text-[var(--ghost)]">
                    <span className="flex items-center gap-[7px]">
                      <span className="block h-px w-3.5 bg-primary" />
                      {t(MECHANISM.legend.flow)}
                    </span>
                    <span className="flex items-center gap-[7px]">
                      <span className="block h-px w-3.5 bg-brand-2" />
                      {t(MECHANISM.legend.debate)}
                    </span>
                    <span>{t(MECHANISM.legend.waves)}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── 窄屏：竖向时间轴 ── */}
      <MechanismTimeline />
    </section>
  );
}

/** 窄屏版：一条渐变竖轴串起四步，随分区进入视口生长。 */
function MechanismTimeline() {
  const { t } = useLang();
  const ref = useRef<HTMLDivElement>(null);
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
      { threshold: 0.12, rootMargin: "0px 0px -10% 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`container-x py-[clamp(3.25rem,7vw,7.5rem)] min-[70rem]:hidden ${
        visible ? "is-visible" : ""
      }`}
    >
      <p className="eyebrow mb-[1.125rem]">{t(MECHANISM.eyebrow)}</p>
      <h2 className="section-title mb-9">{t(MECHANISM.title)}</h2>

      <div className="relative pl-[1.875rem]">
        <span className="absolute bottom-1.5 left-[5px] top-1.5 w-px bg-border" />
        <span className="rail-fill absolute bottom-1.5 left-[5px] top-1.5 w-px bg-gradient-to-b from-primary to-brand-2" />
        {MECHANISM.steps.map((s, i) => (
          <div key={s.idx} className="relative pb-[2.125rem] last:pb-0">
            <span
              className={`absolute -left-[1.875rem] top-[5px] block size-[11px] rounded-full border ${
                i === 0 ? "border-primary bg-primary" : "border-line bg-background"
              }`}
            />
            <div className="mb-[7px] flex items-baseline gap-2.5">
              <span className="font-mono text-[0.65625rem] text-ghost">
                {s.idx}
              </span>
              <span className="text-base font-medium">{t(s.title)}</span>
            </div>
            <p className="m-0 text-[0.84375rem] leading-[1.7] text-muted-foreground">
              {t(s.body)}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
