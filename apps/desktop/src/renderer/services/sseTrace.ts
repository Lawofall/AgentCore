// Dev-only SSE 时序探针（前端侧）。
//
// 服务端 `apps/server/scripts/probe_turn.py` 已证「后端发射 + fold 顺序」忠实；这一件抓的是
// 另一条线——**浏览器内**的实时渲染：React/rAF 合批后，气泡上 `process[]` 的最终顺序是否仍
// 等于事件到达顺序，以及多 Agent 回合的固定布局是否才是「观感乱序」的真正来源。
//
// 默认关闭（零噪音、prod 全量编译掉）。开：DevTools 控制台执行 `__sseTrace()`（或
// `localStorage.sseTrace = "1"` 后刷新），再发一条消息——
//   · 每个非 delta 事件按到达顺序打一行（`+<ms>` 自 message_start 起）；
//   · 连续同类 delta 合成一行（content ×N / reasoning ×N / run·output ×N…），不刷屏；
//   · `message_end` 把「到达折叠序」与气泡最终 `process[].kind` 序并排 dump 并判定：
//       单 Agent 一致 → fold 忠实、无前端重排；不一致 → 重排在前端 mutator/rAF；
//       多 Agent → 气泡走团队图忽略 process[]，屏幕顺序由布局决定（观感乱序多半在此）。
// 关：`__sseTrace(false)`。
//
// 纯诊断、零生产副作用：只读 store 传入的 `process`，不改任何状态。

import type { ProcessStep, SSEEvent, SSEEventType } from "@/types/events";

/** 连续 delta 合批显示用的标签（其余事件逐条作为里程碑打印）。 */
const DELTA_LABEL: Partial<Record<SSEEventType, string>> = {
  content_delta: "content",
  reasoning_delta: "reasoning",
  run_output_delta: "run·output",
  run_reasoning_delta: "run·reasoning",
  run_tool_progress: "run·tool…",
  tool_progress: "compose…",
};

/** 气泡单 Agent process 时间线只含这三种步——到达侧按后端 `_accumulate_process` 同款折叠，
 * 与 store 折出的 `process[].kind` 直接对账。 */
type ProcessKind = "reasoning" | "content" | "tool";

interface ConvTrace {
  start: number;
  /** 是否多 Agent（见到 run_plan）：决定 message_end 的判定文案。 */
  multiAgent: boolean;
  /** 事件总数（含 delta）。 */
  total: number;
  /** 到达侧折叠出的 process 镜像（fold_process 同款规则）。 */
  folded: ProcessKind[];
  // 当前合批中的 delta 段
  curLabel: string | null;
  curCount: number;
  curChars: number;
  curT0: number;
}

const traces = new Map<string, ConvTrace>();

function fresh(): ConvTrace {
  return {
    start: performance.now(),
    multiAgent: false,
    total: 0,
    folded: [],
    curLabel: null,
    curCount: 0,
    curChars: 0,
    curT0: 0,
  };
}

let _on = false;

declare global {
  interface Window {
    /** Dev 时序探针开关（生产不存在）。`__sseTrace()` 开、`__sseTrace(false)` 关。 */
    __sseTrace?: (on?: boolean) => boolean;
  }
}

if (import.meta.env.DEV && typeof window !== "undefined") {
  try {
    _on = window.localStorage?.getItem("sseTrace") === "1";
  } catch {
    /* localStorage 不可用（隐私模式等）——退回内存开关 */
  }
  window.__sseTrace = (on = true): boolean => {
    _on = on;
    try {
      if (on) window.localStorage.setItem("sseTrace", "1");
      else window.localStorage.removeItem("sseTrace");
    } catch {
      /* 持久化失败无妨，本次会话内仍生效 */
    }
    console.info(`[sse-trace] ${on ? "ON — 发一条消息看时序" : "off"}`);
    return _on;
  };
}

/** dev 构建 + 运行时开关同时为真才工作；prod 下 `import.meta.env.DEV` 静态 false，整段编译掉。 */
function enabled(): boolean {
  return import.meta.env.DEV && _on;
}

function short(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function flushLive(t: ConvTrace): void {
  if (t.curLabel === null) return;
  const at = String(t.curT0).padStart(6, " ");
  console.info(
    `  +${at}ms  ~ ${t.curLabel} ×${t.curCount} (${t.curChars} chars)`,
  );
  t.curLabel = null;
  t.curCount = 0;
  t.curChars = 0;
}

/** 把事件折进到达侧的 process 镜像——只有这四类影响单 Agent 气泡时间线。 */
function foldArrival(t: ConvTrace, event: SSEEvent): void {
  const last = (): ProcessKind | undefined => t.folded[t.folded.length - 1];
  switch (event.type) {
    case "reasoning_delta":
      if ((event.payload as { delta?: string }).delta && last() !== "reasoning")
        t.folded.push("reasoning");
      break;
    case "content_delta":
      if ((event.payload as { delta?: string }).delta && last() !== "content")
        t.folded.push("content");
      break;
    case "content_reset":
      while (last() === "content") t.folded.pop();
      break;
    case "tool_use_start":
      t.folded.push("tool");
      break;
    default:
      break;
  }
}

function milestone(event: SSEEvent): string {
  const p = (event.payload ?? {}) as Record<string, unknown>;
  const len = (v: unknown): number => (Array.isArray(v) ? v.length : 0);
  switch (event.type) {
    case "message_start":
      return ">> message_start";
    case "content_reset":
      return "‼ content_reset (丢弃草稿正文)";
    case "tool_use_start":
      return `[tool▶] ${p.tool_name ?? "?"}`;
    case "tool_use_end":
      return `[tool◀] ${p.tool_name ?? "?"} (${p.status ?? "?"})`;
    case "run_plan":
      return `[TEAM] run_plan type=${p.plan_type} agents=${len(p.agents)} runs=${len(p.runs)}`;
    case "run_started":
      return `[run▶] ${String(p.kind ?? "")} ${String(p.agent_id ?? "")}`.trim();
    case "run_completed":
      return `[run✓] ${p.agent_id ?? ""}`;
    case "run_failed":
      return `[run✗] ${p.agent_id ?? ""}`;
    case "run_context":
      return `[ctx] run_context blocks=${len(p.blocks)}`;
    case "citations":
      return `[cite] ×${len(p.citations)}`;
    case "checkpoint_required":
    case "plan_review_required":
      return `[PAUSE] ${event.type}`;
    case "message_end":
      return `== message_end (finish=${p.finish_reason ?? "?"})`;
    case "error":
      return `XX error ${p.code ?? ""}: ${p.message ?? ""}`;
    default:
      return `· ${event.type}`;
  }
}

/**
 * 记一条到达的 SSE 事件（在 `dispatchSSEEvent` 顶部调用，先于任何 store 写入）。
 * 关闭时立即返回；message_start 重置该会话的探针状态。
 */
export function traceSSEEvent(event: SSEEvent, conversationId: string): void {
  if (!enabled()) return;
  if (event.type === "message_start") {
    traces.set(conversationId, fresh());
    console.info(
      `%c[sse-trace] ▼ turn start conv=${short(conversationId)}`,
      "color:#888",
    );
    return;
  }
  // 重连续看 / 回放可能没从 message_start 起步——补一个 trace 容器，时序仍可读（仅起点偏移）。
  let t = traces.get(conversationId);
  if (!t) {
    t = fresh();
    traces.set(conversationId, t);
  }
  t.total++;
  if (event.type === "run_plan") t.multiAgent = true;
  foldArrival(t, event);

  const at = Math.round(performance.now() - t.start);
  const label = DELTA_LABEL[event.type];
  if (label) {
    const delta = (event.payload as { delta?: string }).delta;
    const len = typeof delta === "string" ? delta.length : 0;
    if (t.curLabel === label) {
      t.curCount++;
      t.curChars += len;
    } else {
      flushLive(t);
      t.curLabel = label;
      t.curCount = 1;
      t.curChars = len;
      t.curT0 = at;
    }
    return;
  }
  flushLive(t);
  console.info(`  +${String(at).padStart(6, " ")}ms  ${milestone(event)}`);
}

/**
 * 回合收尾时对账（在 `message_end` / `error` 分支、finalize 之后调用，传入气泡最终
 * `process`）：并排 dump「到达折叠序」与「气泡 process[].kind 序」并判定重排归属。
 */
export function traceTurnEnd(
  conversationId: string,
  process: ProcessStep[] | undefined,
): void {
  if (!enabled()) return;
  const t = traces.get(conversationId);
  if (!t) return;
  flushLive(t);

  const total = Math.round(performance.now() - t.start);
  const arrival = t.folded;
  const stored = (process ?? []).map((s) => s.kind);
  const match =
    arrival.length === stored.length &&
    arrival.every((k, i) => k === stored[i]);

  console.info(
    `[sse-trace] ▲ turn done conv=${short(conversationId)} · ${total}ms · ${t.total} events`,
  );
  console.info(`  到达折叠 arrival : ${arrival.join(" → ") || "(空)"}`);
  console.info(`  气泡 process[]   : ${stored.join(" → ") || "(空)"}`);
  if (t.multiAgent) {
    console.warn(
      "  ⚠ 多 Agent：气泡按团队图渲染、忽略 process[]；屏幕顺序由固定布局" +
        "（思考面板 → 团队图 → 底部正文）决定，与 process 顺序无关——观感乱序多半在此。",
    );
  } else if (stored.length === 0) {
    console.info("  ⓘ process 为空：纯委派 / 无步骤回合。");
  } else if (match) {
    console.info(
      "  ✓ 单 Agent：fold 忠实，气泡内联时间线 = 到达顺序（无前端重排）。",
    );
  } else {
    console.warn(
      "  ✗ 单 Agent 不一致：store 折叠改了顺序——重排根因在前端 mutator/rAF，查这里。",
    );
  }
  traces.delete(conversationId);
}
