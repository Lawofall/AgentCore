"use client";

import { useEffect, useRef, useState } from "react";
import { useLang } from "@/components/LangProvider";
import { CTA, MECHANISM } from "@/content/home";
import { WEB_APP_URL } from "@/lib/download";

/**
 * 四步机制：一条连续的蛇形光路 + 四张错落的卡片 + 末端 CTA。
 *
 * 关键在「一条路径」。光是沿着同一条曲线从头长到尾的，走过的部分一直留着。
 * 早先的版本把它拆成三段各自跑 dashoffset，看上去是「连接线逐段加载」，
 * 而不是一股能量顺着流下去——那是两种完全不同的动效。
 *
 * 进度只有一个 0→1 的值：卡片点亮、光路生长、末端按钮提亮全部由它派生，
 * 所以三者永远不会各走各的。原始滚动进度先过一次「前重后轻」的缓动，再过
 * 一遍弹簧，光头才不会跟着滚轮一格一格地跳。
 *
 * 每帧只写 DOM（dasharray / filter / class），不 setState——这一段一秒要更新
 * 六十次，走 React 重渲染会把整棵卡片树重排一遍。
 */

/* ── 之字形坐标空间 ────────────────────────────────────────
 *
 * 高度写死 1340px 是有意的：一旦让它跟着宽度按比例缩放，窄一点的桌面就会
 * 被拉出一大片空黑，曲线也会被卡片挤扁。宽度上限 1140px，与卡片的百分比
 * 落位配套。
 */
const VW = 1136;
const VH = 1340;

/** 一条连续的蛇形：左上出发 → 右 → 左 → 右下 → 收到正中，正好落在 CTA 上。 */
const FLOW =
  "M80 40C400 40 1100 100 1000 300C900 500 80 460 140 620C200 780 632 760 632 940C632 1080 430 1170 550 1210C670 1250 568 1280 568 1290";

/** 四张卡按百分比落位；第四张回到中右，避免机械的左右重复。 */
const SLOTS: React.CSSProperties[] = [
  { top: "2%", left: "0%" },
  { top: "22%", right: "0%" },
  { top: "46%", left: "0%" },
  { top: "70%", right: "18%" },
];

/** 卡片点亮阈值：光头走到哪张卡的接入点，哪张卡才上色。 */
const CARD_AT = [0, 0.28, 0.55, 0.8];

/** 紫 → 蓝 → 橙 → 绿，与路径渐变逐段对应，走一段换一个色。 */
const ACCENT = ["var(--pp-c1)", "var(--pp-c2)", "var(--pp-c3)", "var(--pp-c4)"];

/** 切换到之字形布局的容器宽（px）。窄于此走竖直单列。 */
const ZIGZAG_AT = 940;

/* 弹簧参数：让光头跟手但不抖。 */
const STIFFNESS = 70;
const DAMPING = 22;
const MASS = 0.7;
const REST = 0.001;

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

/**
 * 前重后轻：70% 的滚动吃掉 80% 的路径。
 * 头几张卡要在标题还没滚出屏幕时就点亮，尾巴那段则留得慢一点，
 * 让最后一张卡和按钮有时间被看见。
 */
const ease = (raw: number) =>
  raw <= 0.7 ? (raw / 0.7) * 0.8 : 0.8 + ((raw - 0.7) / 0.3) * 0.2;

const ICONS = [
  // 你下达目标——一句话
  <path
    key="i0"
    d="M4 5.5h16v10H9l-5 3.5z"
    strokeLinejoin="round"
    strokeLinecap="round"
  />,
  // CEO 组建团队——一组人
  <g key="i1" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="9" cy="8" r="3" />
    <path d="M3 19a6 6 0 0 1 12 0" />
    <path d="M16.5 6.2a3 3 0 0 1 0 5.6M17 14.4A5.5 5.5 0 0 1 21 19" />
  </g>,
  // 分波并行、互审——分支再收拢
  <g key="i2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="5" cy="12" r="2" />
    <circle cx="19" cy="6" r="2" />
    <circle cx="19" cy="18" r="2" />
    <path d="M7 11.2 17 6.6M7 12.8l10 4.6" />
  </g>,
  // 你审阅拍板——确认
  <g key="i3" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="8.2" />
    <path d="m8.4 12.2 2.6 2.6 4.6-5" />
  </g>,
];

export default function ProcessPath() {
  const { t } = useLang();
  const hostRef = useRef<HTMLDivElement>(null);
  /** 滚动进度的量尺：宽屏是那块 1340px 画布，窄屏是竖列本身。 */
  const trackRef = useRef<HTMLDivElement>(null);
  const glowRef = useRef<SVGPathElement>(null);
  const railRef = useRef<HTMLSpanElement>(null);
  const ctaRef = useRef<HTMLDivElement>(null);
  const slotRefs = useRef<(HTMLDivElement | null)[]>([]);
  /** 由动画 effect 装填，供布局切换时再踢一脚。 */
  const kickRef = useRef<((snap?: boolean) => void) | null>(null);
  const [zigzag, setZigzag] = useState(true);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const ro = new ResizeObserver(([e]) =>
      setZigzag(e.contentRect.width >= ZIGZAG_AT),
    );
    ro.observe(host);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    /** 一个进度值 → 全部视觉状态。 */
    const paint = (p: number) => {
      // pathLength=1，所以「已走过的长度」就是进度本身；虚线的空档给足 1，
      // 保证后半段永远不会绕回来重复画。
      if (glowRef.current) glowRef.current.style.strokeDasharray = `${p} 1`;
      if (railRef.current) railRef.current.style.transform = `scaleY(${p})`;
      // 光走到末端时按钮才完全亮起来，之前一直压着亮度和饱和度。
      if (ctaRef.current) {
        const k = clamp01((p - 0.88) / 0.12);
        ctaRef.current.style.filter = `brightness(${0.55 + 0.45 * k}) saturate(${
          0.4 + 0.6 * k
        })`;
      }
      slotRefs.current.forEach((el, i) => {
        el?.classList.toggle("is-active", p > CARD_AT[i]);
      });
    };

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      // 直接停在「走完」那一帧。切换宽窄布局后新节点也要再画一次，
      // 所以这里同样把 kickRef 填上。
      paint(1);
      kickRef.current = () => paint(1);
      return () => {
        kickRef.current = null;
      };
    }

    let raf = 0;
    let last = 0;
    let target = 0;
    let primed = false;
    const spring = { x: 0, v: 0 };

    const step = (now: number) => {
      const dt = last ? Math.min(0.032, (now - last) / 1000) : 1 / 60;
      last = now;

      // 每次都重新取 ref，不吃闭包里的旧节点——宽窄布局切换时它会换人。
      const el = trackRef.current;
      if (el) {
        const r = el.getBoundingClientRect();
        const vh = window.innerHeight;
        /*
         * p=0：区块上缘落到视口 80% 处（刚露头，标题还在画面里）；
         * p=1：区块下缘升到视口正中。
         * 分母要用「高度 + 这两个基准之差」，直接拿高度减一个系数会让进度
         * 在区块刚对齐视口顶时就冲到 0.5 以上，后两张卡在屏幕外就点亮了。
         */
        target = ease(clamp01((vh * 0.8 - r.top) / (r.height + vh * 0.3)));
      }

      // 首帧直接落位，别让页面一加载就自己跑一段动画。
      if (!primed) {
        primed = true;
        spring.x = target;
        paint(target);
        raf = requestAnimationFrame(step);
        return;
      }

      const a = (-STIFFNESS * (spring.x - target) - DAMPING * spring.v) / MASS;
      spring.v += a * dt;
      spring.x += spring.v * dt;

      if (Math.abs(target - spring.x) < REST && Math.abs(spring.v) < REST) {
        spring.x = target;
        spring.v = 0;
        paint(spring.x);
        raf = 0;
        last = 0;
        return;
      }
      paint(spring.x);
      raf = requestAnimationFrame(step);
    };

    /*
     * 弹簧停下后就不再占用每帧；滚动 / 缩放把它重新踢起来。
     * snap=true 时先清掉 primed，让它下一帧直接落位而不是再弹一次——宽窄
     * 布局切换后用得上。注意监听器要包一层：直接把 kick 挂到事件上，Event
     * 对象会被当成 snap 传进来。
     */
    const kick = (snap = false) => {
      if (snap) primed = false;
      if (!raf) {
        last = 0;
        raf = requestAnimationFrame(step);
      }
    };
    kickRef.current = kick;

    const onScroll = () => kick();
    kick();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      kickRef.current = null;
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  /* 宽窄切换会换掉光路 / 竖轨那两个节点，新节点还停在初始值上；而弹簧此时
     多半已经静止，不会再有一帧去刷它。这里补一次落位。 */
  useEffect(() => {
    kickRef.current?.(true);
  }, [zigzag]);

  /* 卡片入场与「点亮」是两件事：入场只看自己有没有进视口（进了就不再退回），
     点亮才跟着光路走。混在一起会出现「卡片还没出现就已经是高亮色」。 */
  useEffect(() => {
    const els = slotRefs.current.filter(Boolean) as HTMLDivElement[];
    if (!els.length) return;

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      els.forEach((el) => el.classList.add("is-in"));
      return;
    }

    const io = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          e.target.classList.add("is-in");
          io.unobserve(e.target);
        }),
      { rootMargin: "0px 0px -100px 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [zigzag]);

  const steps = MECHANISM.steps;

  const card = (i: number) => (
    <article className="pp-card">
      <span className="pp-icon-wrap" aria-hidden="true">
        <span className="pp-icon">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
          >
            {ICONS[i]}
          </svg>
        </span>
      </span>
      <p className="pp-step">{steps[i].idx}</p>
      <h3 className="pp-title">{t(steps[i].title)}</h3>
      <p className="pp-body">{t(steps[i].body)}</p>
    </article>
  );

  /*
   * 宽窄两套布局必须共用同一个根节点。
   * 早先版本在两个分支里各 return 一棵树，切换时根节点被卸载重建，
   * 而 scroll / ResizeObserver 的闭包仍指着已卸载的旧节点——进度卡死在 0，
   * 之后 resize 也不会再触发。根节点稳定，只换内部内容。
   */
  return (
    <div ref={hostRef} className={`pp ${zigzag ? "" : "pp-column"}`.trim()}>
      <div aria-hidden="true" className="pp-aurora" />

      {zigzag ? (
        <div ref={trackRef} className="pp-stage">
          <svg
            aria-hidden="true"
            className="pp-canvas"
            viewBox={`0 0 ${VW} ${VH}`}
            fill="none"
            preserveAspectRatio="none"
          >
            <defs>
              {/* 纵向渐变走的是绝对坐标，所以颜色只跟「走到了多深」有关，
                  跟这一段曲线是往左还是往右拐无关。 */}
              <linearGradient
                id="pp-flow"
                x1="0"
                y1="0"
                x2="0"
                y2="1290"
                gradientUnits="userSpaceOnUse"
              >
                {/* 颜色走 class 而不是 stopColor 属性：presentation attribute
                    里的 var() 不是每个引擎都认。 */}
                <stop offset="0" className="pp-s1" />
                <stop offset="0.33" className="pp-s2" />
                <stop offset="0.67" className="pp-s3" />
                <stop offset="1" className="pp-s4" />
              </linearGradient>
              {/* 用 SVG 滤镜而不是 CSS drop-shadow：光晕要跟着底下那一段的
                  颜色走，drop-shadow 只能给一个固定色。 */}
              <filter
                id="pp-bloom"
                x="-50%"
                y="-50%"
                width="200%"
                height="200%"
              >
                <feGaussianBlur stdDeviation="4" result="bloom" />
                <feMerge>
                  <feMergeNode in="bloom" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>

            <path d={FLOW} className="pp-track" />
            <path
              ref={glowRef}
              d={FLOW}
              className="pp-glow"
              pathLength={1}
              strokeDashoffset={0}
              style={{ strokeDasharray: "0 1" }}
            />
          </svg>

          {SLOTS.map((slot, i) => (
            <div
              key={steps[i].idx}
              ref={(el) => {
                slotRefs.current[i] = el;
              }}
              className={`pp-slot${i === 0 ? " is-in" : ""}`}
              style={
                {
                  ...slot,
                  "--pp-accent": ACCENT[i],
                  transitionDelay: `${i * 100}ms`,
                } as React.CSSProperties
              }
            >
              {card(i)}
            </div>
          ))}
        </div>
      ) : (
        <div ref={trackRef} className="pp-list">
          <span aria-hidden="true" className="pp-rail" />
          <span ref={railRef} aria-hidden="true" className="pp-rail-glow" />
          {steps.map((stepItem, i) => (
            <div
              key={stepItem.idx}
              ref={(el) => {
                slotRefs.current[i] = el;
              }}
              className={`pp-slot pp-slot-row${i === 0 ? " is-in" : ""}`}
              style={
                {
                  "--pp-accent": ACCENT[i],
                  transitionDelay: `${i * 60}ms`,
                } as React.CSSProperties
              }
            >
              <span aria-hidden="true" className="pp-dot" />
              {card(i)}
            </div>
          ))}
        </div>
      )}

      {/* 路径的末端正好落在按钮上：光走完，按钮才被点亮。 */}
      <div ref={ctaRef} className="pp-cta">
        <a
          href={WEB_APP_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="pp-cta-btn"
        >
          <span aria-hidden="true" className="pp-cta-shine" />
          <span className="pp-cta-label">{t(CTA.webApp)}</span>
          <span aria-hidden="true" className="pp-cta-arrow">
            ↗
          </span>
        </a>
      </div>
    </div>
  );
}
