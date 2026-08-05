"use client";

import { useEffect, useRef, useState } from "react";
import { useLang } from "@/components/LangProvider";
import { GRAPH } from "@/content/home";

/**
 * Hero 右侧的「协作图」——产品内协作画布的复刻。
 *
 * 一次真实任务的时间线：你下达目标 → CEO 派出 3 个方向专员并行摸底 →
 * 各自调用不同工具、耗时不同 → 陆续交付 → CEO 汇总成报告。
 *
 * 三个 worker 的 start/run/done 刻意错开：如果三条同时亮同时灭，看起来就是
 * 「一个助手分饰三角」；错开才读得出各自独立推进、CEO 在等一支队伍。
 *
 * 动画由一个 120ms 的 tick 驱动、状态全部从 elapsed 推导（不是一串 setTimeout），
 * 所以循环、暂停、降级都只是改 t 的来源，不会出现半路错位的中间态。
 */

const LOOP = 15_500;

/** 切换到宽几何的容器宽（px）。必须与 globals.css 里 @container 的 35rem 对齐。 */
const WIDE_AT = 560;

/**
 * 每个 worker 的时间线（ms，相对循环起点）。
 * 波次①（0,1）先并行；②（2,3）等①的产出才起步；③（4）最后进场质检。
 * 这份错开就是「按依赖分波」的全部证据，改动时别把它们对齐。
 */
const TIMELINE = [
  { start: 600, run: 1600, done: 4200 },
  { start: 800, run: 1900, done: 4800 },
  { start: 4400, run: 5400, done: 7800 },
  { start: 4900, run: 5900, done: 8500 },
  { start: 8700, run: 9500, done: 11_600 },
];

const ALL_DONE = Math.max(...TIMELINE.map((w) => w.done));
const MERGE_UNTIL = ALL_DONE + 2000;

/** Agent 身份色：与站内协作图同一套，五个角色各占一格。 */
const TONES = [
  "--agent-1",
  "--agent-3",
  "--agent-6",
  "--agent-4",
  "--agent-8",
];

type Geometry = {
  vw: number;
  vh: number;
  task: Box;
  workers: Box[];
  ceo: Box;
  edgeIn: (i: number) => Pt[];
  edgeOut: (i: number) => Pt[];
};
type Box = { x: number; y: number; w: number; h: number };
type Pt = { x: number; y: number };

/* 宽屏：左 → 中 → 右，扇出再收拢，一眼看出并行。 */
const WIDE: Geometry = (() => {
  const workers = [14, 120, 226, 332, 438].map((y) => ({
    x: 240,
    y,
    w: 268,
    h: 94,
  }));
  const cy = (i: number) => workers[i].y + workers[i].h / 2;
  return {
    vw: 720,
    vh: 540,
    task: { x: 4, y: 226, w: 156, h: 88 },
    workers,
    ceo: { x: 552, y: 226, w: 164, h: 88 },
    edgeIn: (i) => [
      { x: 160, y: 270 },
      { x: 200, y: 270 },
      { x: 200, y: cy(i) },
      { x: 240, y: cy(i) },
    ],
    edgeOut: (i) => [
      { x: 508, y: cy(i) },
      { x: 530, y: cy(i) },
      { x: 530, y: 270 },
      { x: 552, y: 270 },
    ],
  };
})();

/* 窄屏：上 → 下，分支走左沟、回流走右沟，保住扇出/收拢的形状。 */
const NARROW: Geometry = (() => {
  /*
   * 卡高不能凭感觉给：卡高与字号都随容器等比缩放（前者 h/vw·W，后者 cqi·W），
   * 比例一旦定错，在任何宽度下都是同样比例地溢出，不会「大屏就好了」。
   * 这里的 84/420 是按实测内容高（≈0.182·容器宽）留出裕度倒推的。
   */
  const workers = [106, 196, 286, 376, 466].map((y) => ({
    x: 56,
    y,
    w: 308,
    h: 84,
  }));
  const cy = (i: number) => workers[i].y + workers[i].h / 2;
  return {
    vw: 420,
    vh: 672,
    task: { x: 112, y: 4, w: 196, h: 78 },
    workers,
    ceo: { x: 112, y: 578, w: 196, h: 82 },
    edgeIn: (i) => [
      { x: 210, y: 82 },
      { x: 210, y: 94 },
      { x: 28, y: 94 },
      { x: 28, y: cy(i) },
      { x: 56, y: cy(i) },
    ],
    edgeOut: (i) => [
      { x: 364, y: cy(i) },
      { x: 392, y: cy(i) },
      { x: 392, y: 564 },
      { x: 210, y: 564 },
      { x: 210, y: 578 },
    ],
  };
})();

/** 轴对齐折线 → 带圆角的 path。拐角半径受相邻线段长度约束，短段不会拱起来。 */
function orthPath(pts: Pt[], r = 12): string {
  if (pts.length < 2) return "";
  let d = `M${pts[0].x} ${pts[0].y}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i];
    const prev = pts[i - 1];
    const next = pts[i + 1];
    const inLen = Math.hypot(p.x - prev.x, p.y - prev.y);
    const outLen = Math.hypot(next.x - p.x, next.y - p.y);
    const rr = Math.min(r, inLen / 2, outLen / 2);
    const inDir = { x: Math.sign(p.x - prev.x), y: Math.sign(p.y - prev.y) };
    const outDir = { x: Math.sign(next.x - p.x), y: Math.sign(next.y - p.y) };
    d += ` L${p.x - inDir.x * rr} ${p.y - inDir.y * rr}`;
    d += ` Q${p.x} ${p.y} ${p.x + outDir.x * rr} ${p.y + outDir.y * rr}`;
  }
  const last = pts[pts.length - 1];
  return `${d} L${last.x} ${last.y}`;
}

const pct = (v: number, total: number) => `${(v / total) * 100}%`;

type Stage = "idle" | "thinking" | "running" | "done";

function stageOf(t: number, i: number): Stage {
  const w = TIMELINE[i];
  if (t < w.start) return "idle";
  if (t < w.run) return "thinking";
  if (t < w.done) return "running";
  return "done";
}

export default function CollabGraph() {
  const { t: tr } = useLang();
  const hostRef = useRef<HTMLDivElement>(null);
  const [elapsed, setElapsed] = useState(0);
  // 首屏按宽屏渲染，挂载后再按实际容器切——与 SSR 输出一致，不会 hydration 打架。
  const [wide, setWide] = useState(true);

  /*
   * 几何按「容器宽」切，不是视口宽。
   * Hero 在 lg 以上是两栏，右栏比视口窄得多（视口 1024px 时容器只有 ~430px），
   * 若这里看视口、字号看 cqi 看容器，两者就会背离成「宽几何 + 窄字号」，
   * 文字直接撑破卡片。阈值必须与 globals.css 里 @container 的 35rem 一致。
   */
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const ro = new ResizeObserver(([entry]) =>
      setWide(entry.contentRect.width >= WIDE_AT),
    );
    ro.observe(host);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    // 降级：直接停在「三人都已交付、CEO 汇总完成」那一帧——静态也讲得完整个故事。
    if (reduced.matches) {
      setElapsed(MERGE_UNTIL + 600);
      return;
    }

    let timer: number | undefined;
    const start = performance.now();
    const tick = () => setElapsed((performance.now() - start) % LOOP);
    const run = () => {
      if (timer) return;
      timer = window.setInterval(tick, 120);
    };
    const stop = () => {
      if (!timer) return;
      window.clearInterval(timer);
      timer = undefined;
    };

    // 滚出视口就停表：Hero 在首屏，往下读的时候没必要一直跑。
    const host = hostRef.current;
    const io = host
      ? new IntersectionObserver(
          ([entry]) => (entry.isIntersecting ? run() : stop()),
          { threshold: 0.05 },
        )
      : null;
    if (io && host) io.observe(host);
    else run();

    const onVisibility = () =>
      document.hidden ? stop() : hostRef.current && run();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      stop();
      io?.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const g = wide ? WIDE : NARROW;
  const stages = TIMELINE.map((_, i) => stageOf(elapsed, i));
  const doneCount = stages.filter((s) => s === "done").length;
  const secs = (from: number) => Math.max(0, Math.round((elapsed - from) / 1000));

  const ceoTone: "wait" | "merge" | "ready" =
    elapsed < ALL_DONE ? "wait" : elapsed < MERGE_UNTIL ? "merge" : "ready";
  const ceoLabel =
    ceoTone === "wait"
      ? `${tr(GRAPH.ceo.waiting)} (${doneCount}/${TIMELINE.length}) · ${tr(GRAPH.ceo.elapsed)} ${secs(TIMELINE[0].start)}s`
      : ceoTone === "merge"
        ? `${tr(GRAPH.ceo.merging)} · ${secs(ALL_DONE)}s`
        : tr(GRAPH.ceo.ready);

  return (
    <div ref={hostRef} className="cg-shell">
      {/* 产品顶栏——让这块一眼读成「真实界面」而不是示意图 */}
      <div className="cg-toolbar">
        <span className="font-semibold">{tr(GRAPH.toolbarTitle)}</span>
        <span className="flex items-center gap-1.5">
          <span className="cg-chip" style={{ color: "oklch(0.6 0.19 25)" }}>
            <span aria-hidden="true">■</span>
            {tr(GRAPH.toolbarStop)}
          </span>
          <span className="cg-chip">{tr(GRAPH.toolbarView)}</span>
        </span>
      </div>

      <div className="cg" style={{ aspectRatio: `${g.vw} / ${g.vh}` }}>
        {/* 点阵画布 */}
        <div aria-hidden="true" className="cg-canvas" />

      {/* 连线：底层是描边（随节点激活「长」出来），上层是流动虚线（仅运行中显示） */}
      <svg
        aria-hidden="true"
        className="absolute inset-0 h-full w-full"
        viewBox={`0 0 ${g.vw} ${g.vh}`}
        fill="none"
      >
        {g.workers.map((_, i) => {
          const on = stages[i] !== "idle";
          const flowing = stages[i] === "thinking" || stages[i] === "running";
          return (
            <g key={`in-${i}`}>
              <path
                d={orthPath(g.edgeIn(i))}
                pathLength={100}
                className={`cg-edge cg-edge-in ${on ? "is-on" : ""}`}
              />
              {flowing && (
                <path d={orthPath(g.edgeIn(i))} className="cg-flow" />
              )}
            </g>
          );
        })}
        {g.workers.map((_, i) => (
          <path
            key={`out-${i}`}
            d={orthPath(g.edgeOut(i))}
            pathLength={100}
            className={`cg-edge ${stages[i] === "done" ? "is-on" : ""}`}
          />
        ))}
      </svg>

      {/* 你的任务 */}
      <div
        className="cg-node cg-node-task"
        style={{
          left: pct(g.task.x, g.vw),
          top: pct(g.task.y, g.vh),
          width: pct(g.task.w, g.vw),
          height: pct(g.task.h, g.vh),
        }}
      >
        <div className="flex items-center gap-[0.55em]">
          <span className="cg-avatar cg-avatar-user" aria-hidden="true">
            <svg viewBox="0 0 16 16" className="w-[0.85em]" fill="currentColor">
              <circle cx="8" cy="5" r="2.8" />
              <path d="M2.6 14a5.4 5.4 0 0 1 10.8 0z" />
            </svg>
          </span>
          <div className="min-w-0">
            <p className="cg-title">{tr(GRAPH.task.title)}</p>
            <p className="cg-sub">{tr(GRAPH.task.sub)}</p>
          </div>
        </div>
        <p className="cg-meta">{tr(GRAPH.toolbarTitle)}</p>
      </div>

      {/* 三个方向专员 */}
      {g.workers.map((box, i) => {
        const stage = stages[i];
        const spec = GRAPH.workers[i];
        const active = stage === "thinking" || stage === "running";
        const note = tr(spec.note);
        // 运行中让 note 逐字打出来：静止的省略号看着像卡住，打字才像在干活。
        const typed =
          stage === "running"
            ? note.slice(
                0,
                Math.max(
                  1,
                  Math.round(
                    ((elapsed - TIMELINE[i].run) /
                      (TIMELINE[i].done - TIMELINE[i].run)) *
                      note.length *
                      1.35,
                  ),
                ),
              )
            : stage === "done"
              ? note
              : "";

        return (
          <div
            key={i}
            className={`cg-node cg-node-worker ${active ? "is-active" : ""} ${
              stage === "done" ? "is-done" : ""
            } ${stage === "idle" ? "is-idle" : ""}`}
            style={{
              left: pct(box.x, g.vw),
              top: pct(box.y, g.vh),
              width: pct(box.w, g.vw),
              height: pct(box.h, g.vh),
            }}
          >
            <div className="flex items-center gap-[0.5em]">
              <span
                className="cg-avatar"
                aria-hidden="true"
                style={{
                  background: `color-mix(in oklab, var(${TONES[i]}), white 72%)`,
                  color: `color-mix(in oklab, var(${TONES[i]}), black 34%)`,
                }}
              >
                {tr(spec.name).slice(0, 1)}
              </span>
              <p className="cg-title">{tr(spec.name)}</p>
              <span className="cg-wave" aria-hidden="true">
                {spec.wave}
              </span>
              <span className="flex-1" />
              {stage === "done" && (
                <span className="cg-check" aria-hidden="true">
                  ✓
                </span>
              )}
            </div>

            <p className={`cg-status ${stage === "done" ? "is-done" : ""}`}>
              {stage === "idle"
                ? tr(GRAPH.queued)
                : stage === "thinking"
                  ? `${tr(GRAPH.thinking)} · ${secs(TIMELINE[i].start)}s`
                  : stage === "running"
                    ? `${spec.tool} · ${secs(TIMELINE[i].start)}s`
                    : `${tr(GRAPH.finished)} · ${Math.round((TIMELINE[i].done - TIMELINE[i].start) / 1000)}s`}
            </p>

            <p className="cg-note">
              {typed}
              {stage === "running" && <span className="cg-caret" />}
            </p>
          </div>
        );
      })}

      {/* CEO 汇总 */}
      <div
        className={`cg-node cg-node-ceo ${ceoTone === "ready" ? "is-done" : "is-active"}`}
        style={{
          left: pct(g.ceo.x, g.vw),
          top: pct(g.ceo.y, g.vh),
          width: pct(g.ceo.w, g.vw),
          height: pct(g.ceo.h, g.vh),
        }}
      >
        <div className="flex items-center gap-[0.55em]">
          <span className="cg-avatar cg-avatar-ceo" aria-hidden="true">
            {ceoTone === "ready" ? "✓" : <span className="cg-spin" />}
          </span>
          <p className="cg-title flex-1">{tr(GRAPH.ceo.title)}</p>
        </div>
          <p className={`cg-status ${ceoTone === "ready" ? "is-done" : ""}`}>
            {ceoLabel}
          </p>
          <p className="cg-note not-italic">{tr(GRAPH.ceo.body)}</p>
        </div>
      </div>
    </div>
  );
}
