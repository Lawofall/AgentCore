import type { RunStatus } from "@/stores/execution";
import type { GraphEdge } from "@/stores/graph";
import { RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { EmbeddedGraphCanvas } from "./EmbeddedGraphCanvas";
import { usePrefersReducedMotion } from "./shared";

// 两段同时开，再交给下一段封口。形态静态，状态由 HERO_PHASES 逐帧外注。
const HERO_NODES = [
  {
    id: "__input__",
    type: "userInput" as const,
    data: {
      variant: "input",
      status: "completed",
      label: "帮我做一份近 7 日成本分析报告",
    },
  },
  // outputPreview 刻意压成一行（≈ 卡片单行宽）：running 显流式预览、完成回落 task，两者
  // 同为一行 → 逐帧切状态时节点不变高、不重排，循环播放稳。
  {
    id: "ha",
    type: "agent" as const,
    data: {
      agentId: "ha",
      runId: "ha",
      role: "调研员",
      status: "pending",
      isAnimating: false,
      task: "检索成本数据源与口径",
      outputPreview: "正在检索数据源…",
      tokenCount: 0,
      toolCount: 0,
      focused: false,
    },
  },
  {
    id: "hb",
    type: "agent" as const,
    data: {
      agentId: "hb",
      runId: "hb",
      role: "调研员",
      status: "pending",
      isAnimating: false,
      task: "收集历史基准与区间",
      outputPreview: "正在拉取基准区间…",
      tokenCount: 0,
      toolCount: 0,
      focused: false,
    },
  },
  {
    id: "han",
    type: "agent" as const,
    data: {
      agentId: "han",
      runId: "han",
      role: "数据分析师",
      status: "pending",
      isAnimating: false,
      task: "汇总趋势、定位异常点",
      outputPreview: "正在比对、定位异常…",
      tokenCount: 0,
      toolCount: 0,
      focused: false,
    },
  },
];

const HERO_EDGES: GraphEdge[] = [
  { id: "__input__->ha", source: "__input__", target: "ha", kind: "dep" },
  { id: "__input__->hb", source: "__input__", target: "hb", kind: "dep" },
  { id: "ha->han", source: "ha", target: "han", kind: "dep" },
  { id: "hb->han", source: "hb", target: "han", kind: "dep" },
];

const HERO_PHASES: { label: string; statuses: Record<string, RunStatus> }[] = [
  {
    label: "你发出目标，段就位",
    statuses: { ha: "pending", hb: "pending", han: "pending" },
  },
  {
    label: "两段同时开",
    statuses: { ha: "running", hb: "running", han: "pending" },
  },
  {
    label: "前两段封口，下一段接着做",
    statuses: {
      ha: "completed",
      hb: "completed",
      han: "running",
    },
  },
  {
    label: "最后一段封口，答案落进正文",
    statuses: {
      ha: "completed",
      hb: "completed",
      han: "completed",
    },
  },
];

const HERO_INPUT_DONE: Record<string, RunStatus> = { __input__: "completed" };

/** ⓪ hero 活图：自动循环播放一回合生命周期 + 逐帧 narration + 重播。 */
export function HeroGraph() {
  const reduced = usePrefersReducedMotion();
  const [phase, setPhase] = useState(0);
  const last = HERO_PHASES.length - 1;

  useEffect(() => {
    if (reduced) {
      setPhase(last);
      return;
    }
    // 终帧多停一会儿让人看清「完成」，其余帧匀速推进；setTimeout 以 phase 为键自然续帧。
    const delay = phase === last ? 2600 : 1500;
    const t = setTimeout(
      () => setPhase((p) => (p + 1) % HERO_PHASES.length),
      delay,
    );
    return () => clearTimeout(t);
  }, [phase, reduced, last]);

  const statuses = useMemo(
    () => ({ ...HERO_INPUT_DONE, ...HERO_PHASES[phase].statuses }),
    [phase],
  );
  const done = phase === last;

  return (
    <div>
      <EmbeddedGraphCanvas
        nodes={HERO_NODES}
        edges={HERO_EDGES}
        layoutKind="leftright"
        statuses={statuses}
      />
      <div className="mt-2 flex items-center gap-2.5">
        <span className="relative flex size-2 shrink-0">
          {!done && !reduced && (
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60" />
          )}
          <span
            className={`relative inline-flex size-2 rounded-full ${done ? "bg-success" : "bg-primary"}`}
          />
        </span>
        <p className="min-w-0 flex-1 text-xs text-muted-foreground">
          <span className="tabular-nums text-muted-foreground/60">
            {phase + 1}/{HERO_PHASES.length}
          </span>
          {" · "}
          <span className="text-foreground">{HERO_PHASES[phase].label}</span>
        </p>
        {!reduced && (
          <button
            type="button"
            onClick={() => setPhase(0)}
            className="flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <RotateCcw size={12} />
            重播
          </button>
        )}
      </div>
    </div>
  );
}
