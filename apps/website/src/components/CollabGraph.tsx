"use client";

import { useEffect, useRef, useState } from "react";
import { useLang } from "@/components/LangProvider";
import { GRAPH } from "@/content/home";

/**
 * Hero 右侧的「协作图」——产品内协作画布的复刻。
 *
 * 一次真实任务的编排：你下达目标 →（① 两个并行摸底）→ 闸门 →（② 两个接着算）
 * → 闸门 →（③ 质检复核）→ CEO 汇总成报告。
 *
 * 五个 worker 沿 X 轴按波次落位，只有同波次的人才上下并排——**依赖关系写在几何里**。
 * 这一点是这张图存在的全部理由：若五个并排成一列，读者看到的就是「一个助手分饰五角
 * 同时开工」，正是本站要反的那件事。
 *
 * 两个闸门（gate）是叙事核心：上一波「全部」交付才放行下一波，所以 ① 里跑得快的
 * 那个会先停在「已完成」等同伴——这一等就是依赖的证据。改时间线时别把它等没了。
 *
 * 动画由一个 120ms 的 tick 驱动、状态全部从 elapsed 推导（不是一串 setTimeout），
 * 所以循环、暂停、降级都只是改 t 的来源，不会出现半路错位的中间态。
 */

const LOOP = 15_800;

/** 切换到宽几何的容器宽（px）。必须与 globals.css 里 @container 的 35rem 对齐。 */
const WIDE_AT = 560;

/**
 * 每个 worker 的时间线（ms，相对循环起点）。
 * 必须严格服从波次闸门：下一波的 start 要晚于上一波所有人的 done，
 * 否则画面上「闸门还没开、人已经在跑」，这张图就自己拆自己的台。
 */
const TIMELINE = [
  { start: 500, run: 1400, done: 3900 },
  { start: 700, run: 1700, done: 4500 },
  { start: 4700, run: 5500, done: 7800 },
  { start: 4900, run: 5800, done: 8400 },
  { start: 8600, run: 9300, done: 11_400 },
];

/** 波次分组。索引即 TIMELINE / GRAPH.workers 的下标。 */
const WAVES = [
  [0, 1],
  [2, 3],
  [4],
];

const ALL_DONE = Math.max(...TIMELINE.map((w) => w.done));
const MERGE_UNTIL = ALL_DONE + 2000;

/** Agent 身份色：与站内协作图同一套，五个角色各占一格。 */
const TONES = ["--agent-1", "--agent-3", "--agent-6", "--agent-4", "--agent-8"];

type Box = { x: number; y: number; w: number; h: number };
type Pt = { x: number; y: number };
/** drive = 由第几个 worker 的状态驱动；push = 派活（进节点），hand = 交付（出节点）。 */
type Edge = { pts: Pt[]; drive: number; kind: "push" | "hand" };
/** 依赖闸门：after 波全部 done 才点亮。r 按几何单位给——两套 vw 不同，不能共用一个值。 */
type Gate = { x: number; y: number; after: number; r: number };

type Geometry = {
  vw: number;
  vh: number;
  task: Box;
  workers: Box[];
  ceo: Box;
  edges: Edge[];
  gates: Gate[];
};

/* ── 宽几何：任务 → ① → 闸门 → ② → 闸门 → ③ → CEO，沿 X 轴分波 ────
 *
 * 分波全靠列位置和两个闸门说话：不画泳道底、不写「波次 ①②③」标签——
 * 三块灰底会把注意力从卡片抢走，而列已经把同波次的人对齐了。
 *
 * 卡宽 0.176·vw（84rem 版心两栏下约 131px）。首屏版心已为这张图放宽，
 * 多出来的宽度刻意「不」给卡片、全给列间沟——卡片贴着卡片时连线短得像缝隙，
 * 任务在列间「流动」的叙事根本展不开。卡面的减法仍然成立：波次标记只留给
 * 窄几何，✓ 移进状态行，宽几何只留「头像 + 名字 / 状态 / 工具回显」，
 * 改动时别把它们加回来。
 *
 * 两个比例分别由不同的数管，别搞混：
 *   · 卡片形状 = CW : CH。卡片的 px 宽高都是 (?/vw)·容器宽，vh 被约掉了——
 *     所以改 vh 不会让方卡变扁，只有压 CH 才行。
 *   · 画布形状 = vw : vh。这里取 1000:560 ≈ 16:9，叠上顶栏和浏览器地址栏后
 *     整框约 3:2——营销图里浏览器 mockup 的经典比例。行间距贴着主干线收紧
 *     （行距一放大，小卡就浮在大片空点阵上，「空旷」反过来放大局促感），
 *     高度盈余留在画布上下边缘（各 56）和卡高里，不要留在节点之间。
 * CH 的下限是内容高：标题 1.5em + 状态 1.15em + 回显两行 2.03em + 间距/内边距
 * 1.64em ≈ 6.32em；字号 2.05cqi 下 1em = 20.5 单位，换算 130。156 的盈余
 * 进了上下内边距（卡面透气），压回 140 以下就开始切字。
 */
const WIDE: Geometry = (() => {
  const CW = 176; // worker 卡宽
  const CH = 156; // worker 卡高（下限 139，见上；盈余进内边距，卡面更透气）
  const COL = [174, 394, 614]; // 三个波次的列起点（列间沟 44，闸门居中）
  const TOP = 134; // 上行卡片中线
  const BOT = 426; // 下行卡片中线
  const MID = (TOP + BOT) / 2; // 主干中线：任务 / 闸门 / ③ / CEO 都落在这条线上
  const rowY = (cy: number) => cy - CH / 2;
  const gate = (i: number) => COL[i] + CW + 22; // 闸门 x：落在两条泳道之间的沟里

  const G1 = gate(0);
  const G2 = gate(1);
  const TASK_R = 136; // 任务卡右缘
  const FAN = 155; // 任务扇出的竖沟
  const CEO_X = 822;

  return {
    vw: 1000,
    vh: 560,
    /* 任务卡缩成一枚方章：顶栏已经写了「5 个 worker · 按依赖分波」，
       卡面再重复一遍纯属浪费。124 是下限——「你的任务」四个字 4em + 内边距
       正好用完，再窄就截字。 */
    task: { x: 12, y: MID - 61, w: 124, h: 122 },
    workers: [
      { x: COL[0], y: rowY(TOP), w: CW, h: CH },
      { x: COL[0], y: rowY(BOT), w: CW, h: CH },
      { x: COL[1], y: rowY(TOP), w: CW, h: CH },
      { x: COL[1], y: rowY(BOT), w: CW, h: CH },
      { x: COL[2], y: rowY(MID), w: CW, h: CH },
    ],
    /* CEO 状态行已缩成「等待 0/5 · 1s」（不折行），卡高就跟 worker 一档。 */
    ceo: { x: CEO_X, y: MID - CH / 2, w: 168, h: CH },
    edges: [
      // 任务扇出到波次 ①
      { pts: [{ x: TASK_R, y: MID }, { x: FAN, y: MID }, { x: FAN, y: TOP }, { x: COL[0], y: TOP }], drive: 0, kind: "push" },
      { pts: [{ x: TASK_R, y: MID }, { x: FAN, y: MID }, { x: FAN, y: BOT }, { x: COL[0], y: BOT }], drive: 1, kind: "push" },
      // ① 交付 → 闸门 1
      { pts: [{ x: COL[0] + CW, y: TOP }, { x: G1, y: TOP }, { x: G1, y: MID }], drive: 0, kind: "hand" },
      { pts: [{ x: COL[0] + CW, y: BOT }, { x: G1, y: BOT }, { x: G1, y: MID }], drive: 1, kind: "hand" },
      // 闸门 1 放行 → 波次 ②
      { pts: [{ x: G1, y: MID }, { x: G1, y: TOP }, { x: COL[1], y: TOP }], drive: 2, kind: "push" },
      { pts: [{ x: G1, y: MID }, { x: G1, y: BOT }, { x: COL[1], y: BOT }], drive: 3, kind: "push" },
      // ② 交付 → 闸门 2
      { pts: [{ x: COL[1] + CW, y: TOP }, { x: G2, y: TOP }, { x: G2, y: MID }], drive: 2, kind: "hand" },
      { pts: [{ x: COL[1] + CW, y: BOT }, { x: G2, y: BOT }, { x: G2, y: MID }], drive: 3, kind: "hand" },
      // 闸门 2 放行 → 波次 ③ → CEO
      { pts: [{ x: G2, y: MID }, { x: COL[2], y: MID }], drive: 4, kind: "push" },
      { pts: [{ x: COL[2] + CW, y: MID }, { x: CEO_X, y: MID }], drive: 4, kind: "hand" },
    ],
    gates: [
      { x: G1, y: MID, after: 0, r: 9 },
      { x: G2, y: MID, after: 1, r: 9 },
    ],
  };
})();

/* ── 窄几何：同一张 DAG 竖过来，波次变成「行」，同波次左右并排。 ──
 *
 * 卡高/卡宽都按容器宽等比缩放（cqi），比例定错就是在任何宽度下同样比例地溢出，
 * 不会「大屏就好了」。这里的 126/700 是按实测内容高留裕度倒推的。
 */
const NARROW: Geometry = (() => {
  const CW = 196;
  const CH = 106;
  const L = 6; // 左列 x
  const R = 218; // 右列 x
  const LX = L + CW / 2; // 左列中线
  const RX = R + CW / 2; // 右列中线
  const MX = 210; // 主干中线
  const ROW = [126, 272, 418]; // 三个波次的行起点
  const bot = (i: number) => ROW[i] + CH;
  const g1 = bot(0) + 20;
  const g2 = bot(1) + 20;
  const CEO_Y = 554;

  return {
    vw: 420,
    vh: 660,
    task: { x: 110, y: 6, w: 200, h: 82 },
    workers: [
      { x: L, y: ROW[0], w: CW, h: CH },
      { x: R, y: ROW[0], w: CW, h: CH },
      { x: L, y: ROW[1], w: CW, h: CH },
      { x: R, y: ROW[1], w: CW, h: CH },
      { x: 112, y: ROW[2], w: CW, h: CH },
    ],
    ceo: { x: 90, y: CEO_Y, w: 240, h: 96 },
    edges: [
      { pts: [{ x: MX, y: 88 }, { x: MX, y: 106 }, { x: LX, y: 106 }, { x: LX, y: ROW[0] }], drive: 0, kind: "push" },
      { pts: [{ x: MX, y: 88 }, { x: MX, y: 106 }, { x: RX, y: 106 }, { x: RX, y: ROW[0] }], drive: 1, kind: "push" },
      { pts: [{ x: LX, y: bot(0) }, { x: LX, y: g1 }, { x: MX, y: g1 }], drive: 0, kind: "hand" },
      { pts: [{ x: RX, y: bot(0) }, { x: RX, y: g1 }, { x: MX, y: g1 }], drive: 1, kind: "hand" },
      { pts: [{ x: MX, y: g1 }, { x: LX, y: g1 }, { x: LX, y: ROW[1] }], drive: 2, kind: "push" },
      { pts: [{ x: MX, y: g1 }, { x: RX, y: g1 }, { x: RX, y: ROW[1] }], drive: 3, kind: "push" },
      { pts: [{ x: LX, y: bot(1) }, { x: LX, y: g2 }, { x: MX, y: g2 }], drive: 2, kind: "hand" },
      { pts: [{ x: RX, y: bot(1) }, { x: RX, y: g2 }, { x: MX, y: g2 }], drive: 3, kind: "hand" },
      { pts: [{ x: MX, y: g2 }, { x: MX, y: ROW[2] }], drive: 4, kind: "push" },
      { pts: [{ x: MX, y: bot(2) }, { x: MX, y: CEO_Y }], drive: 4, kind: "hand" },
    ],
    gates: [
      { x: MX, y: g1, after: 0, r: 5 },
      { x: MX, y: g2, after: 1, r: 5 },
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
   * Hero 在 xl 以上是两栏，右栏比视口窄得多，若这里看视口、字号看 cqi 看容器，
   * 两者就会背离成「宽几何 + 窄字号」，文字直接撑破卡片。
   * 阈值必须与 globals.css 里 @container 的 35rem 一致。
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
    // 降级：停在「五人都已交付、CEO 汇总完成」那一帧——静态也讲得完整个故事。
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
  const gateOpen = (wave: number) => WAVES[wave].every((i) => stages[i] === "done");

  const ceoTone: "wait" | "merge" | "ready" =
    elapsed < ALL_DONE ? "wait" : elapsed < MERGE_UNTIL ? "merge" : "ready";
  /* 等待态压成「等待 0/5 · 1s」：宽几何 CEO 卡宽 0.168·vw，
     带括号带「已等」的长写法必然折行，而且折点会落在词中间。 */
  const ceoLabel =
    ceoTone === "wait"
      ? `${tr(GRAPH.ceo.waiting)} ${doneCount}/${TIMELINE.length} · ${secs(TIMELINE[0].start)}s`
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

        {/* 连线 + 闸门 */}
        <svg
          aria-hidden="true"
          className="absolute inset-0 h-full w-full"
          viewBox={`0 0 ${g.vw} ${g.vh}`}
          fill="none"
        >
          {g.edges.map((e, i) => {
            const stage = stages[e.drive];
            const on = e.kind === "push" ? stage !== "idle" : stage === "done";
            const flowing =
              e.kind === "push" && (stage === "thinking" || stage === "running");
            const d = orthPath(e.pts);
            return (
              <g key={`edge-${i}`}>
                <path
                  d={d}
                  pathLength={100}
                  className={`cg-edge ${e.kind === "push" ? "cg-edge-in" : ""} ${on ? "is-on" : ""}`}
                />
                {flowing && <path d={d} className="cg-flow" />}
              </g>
            );
          })}

          {g.gates.map((gate, i) => (
            <circle
              key={`gate-${i}`}
              className={`cg-gate ${gateOpen(gate.after) ? "is-open" : ""}`}
              cx={gate.x}
              cy={gate.y}
              r={gate.r}
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
          {/* 宽几何下这一行被 CSS 转成竖排：任务卡只有 0.124·vw，
              「头像 + 你的任务」横着放会把标题截掉。 */}
          <div className="cg-task-head flex items-center gap-[0.45em]">
            <span className="cg-avatar cg-avatar-user" aria-hidden="true">
              <svg viewBox="0 0 16 16" className="w-[0.85em]" fill="currentColor">
                <circle cx="8" cy="5" r="2.8" />
                <path d="M2.6 14a5.4 5.4 0 0 1 10.8 0z" />
              </svg>
            </span>
            <div className="min-w-0">
              <p className="cg-title">{tr(GRAPH.task.title)}</p>
            </div>
          </div>
        </div>

        {/* 五个 worker，按波次落位 */}
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
              <div className="flex items-center gap-[0.4em]">
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
                <span className="flex-1" />
                {/* 波次标记只在窄几何出现：宽几何下泳道已经把波次说清楚了。 */}
                <span className="cg-wave" aria-hidden="true">
                  {spec.wave}
                </span>
              </div>

              <p className={`cg-status ${stage === "done" ? "is-done" : ""}`}>
                {stage === "done" && (
                  <span className="cg-check" aria-hidden="true">
                    ✓{" "}
                  </span>
                )}
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
          <div className="flex items-center gap-[0.45em]">
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
