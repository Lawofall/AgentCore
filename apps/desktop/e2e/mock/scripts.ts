/**
 * Vector scripts for e2e mock.
 *
 * §八 切段落点：运行期按事件类型识别边界（不用导出期标注）。
 * - hot（approval）：在 `*_required` 后暂停同流，等 POST interactions 再续推后续段
 * - cold（team_preview）：首段用带 `message_end(paused)` 的 finalized 向量；
 *   resume 段从 `*_resolved_continue` 的 `*_resolved` 起推（事件仍全部来自真实向量）
 */
import {
  type ConformanceEvent,
  type ConformanceFixture,
  loadFixture,
} from "./fixtures.ts";

export type ScriptKind = "complete" | "hot_gate" | "cold_gate";

export interface ScriptPlan {
  name: string;
  kind: ScriptKind;
  /** Events to push on POST .../messages (and hold open if hot_gate). */
  initial: ConformanceEvent[];
  /** Events to push after POST .../interactions (hot only). */
  continueSameStream: ConformanceEvent[];
  /** Events to push on POST .../resume (cold only). */
  resumeStream: ConformanceEvent[];
}

const HOT_REQUIRED = new Set([
  "approval_required",
  "client_tool_required",
  "escalation_required",
]);

const COLD_REQUIRED = new Set([
  "team_preview_required",
  "plan_review_required",
  "checkpoint_required",
  "ask_user_required",
]);

function indexOfType(events: ConformanceEvent[], type: string): number {
  return events.findIndex((e) => e.type === type);
}

function splitHot(fixture: ConformanceFixture): ScriptPlan {
  const idx = fixture.events.findIndex((e) => HOT_REQUIRED.has(e.type));
  if (idx < 0) {
    throw new Error(
      `Script ${fixture.name}: expected a hot *_required boundary`,
    );
  }
  return {
    name: fixture.name,
    kind: "hot_gate",
    initial: fixture.events.slice(0, idx + 1),
    continueSameStream: fixture.events.slice(idx + 1),
    resumeStream: [],
  };
}

/**
 * Cold gate: pin finalized (paused close) for the first SSE, and the
 * resolved_continue vector's post-gate tail for POST resume.
 */
function splitColdTeamPreview(): ScriptPlan {
  const finalized = loadFixture("team_preview_finalized");
  const cont = loadFixture("team_preview_resolved_continue");
  const resolvedIdx = indexOfType(cont.events, "team_preview_resolved");
  if (resolvedIdx < 0) {
    throw new Error(
      "team_preview_resolved_continue missing team_preview_resolved",
    );
  }
  const requiredIdx = indexOfType(finalized.events, "team_preview_required");
  if (requiredIdx < 0) {
    throw new Error("team_preview_finalized missing team_preview_required");
  }
  return {
    name: "team_preview_resolved_continue",
    kind: "cold_gate",
    initial: finalized.events,
    continueSameStream: [],
    resumeStream: cont.events.slice(resolvedIdx),
  };
}

function complete(name: string): ScriptPlan {
  const fixture = loadFixture(name);
  return {
    name,
    kind: "complete",
    initial: fixture.events,
    continueSameStream: [],
    resumeStream: [],
  };
}

/**
 * 浏览器活动卡（聊天时间线）：从 `multi_agent_browser_session` 抽 ≥2 个
 * browser_* 步，去掉 `run_id`，落到消息 process（非 run.process），
 * 否则活动卡只在 run 详情里、聊天看不见。
 */
function browserActivityCard(): ScriptPlan {
  const fixture = loadFixture("multi_agent_browser_session");
  const browserEvents = fixture.events
    .filter(
      (ev) =>
        (ev.type === "tool_use_start" || ev.type === "tool_use_end") &&
        String((ev.payload ?? {}).tool_name ?? "").startsWith("browser_"),
    )
    .map((ev) => {
      const payload = { ...(ev.payload ?? {}) };
      delete payload.run_id;
      // 帧路径会打 workspace 拉取——e2e mock 无文件，去掉以免噪音。
      const display = payload.display as Record<string, unknown> | undefined;
      if (display && "frame" in display) {
        const { frame: _frame, ...rest } = display;
        payload.display = rest;
      }
      return { ...ev, payload };
    });
  if (browserEvents.length < 4) {
    throw new Error(
      "browser_activity_card: expected ≥2 browser tool start/end pairs",
    );
  }
  const initial: ConformanceEvent[] = [
    {
      type: "message_start",
      payload: { message_id: "m1", conversation_id: "conv_demo" },
      timestamp: "2026-01-01T00:00:00.001Z",
    },
    {
      type: "content_delta",
      payload: { delta: "我来打开页面看一下。" },
      timestamp: "2026-01-01T00:00:00.002Z",
    },
    ...browserEvents,
    {
      type: "content_delta",
      payload: { delta: " 已查看目标页。" },
      timestamp: "2026-01-01T00:00:00.090Z",
    },
    {
      type: "message_end",
      payload: {
        finish_reason: "end_turn",
        usage: {
          input_tokens: 100,
          output_tokens: 40,
          reasoning_tokens: 0,
          cache_hit_tokens: 0,
          cache_miss_tokens: 0,
        },
      },
      timestamp: "2026-01-01T00:00:00.091Z",
    },
  ];
  return {
    name: "browser_activity_card",
    kind: "complete",
    initial,
    continueSameStream: [],
    resumeStream: [],
  };
}

/**
 * browser_login escalate：复用阻塞 escalate pending 向量，仅把
 * `escalation_required.payload.browser_login` 钉成 true（e2e 壳测 CTA，
 * 无独立 conformance 向量）。
 */
function browserLoginEscalate(): ScriptPlan {
  const fixture = loadFixture("multi_agent_blocking_escalate_pending");
  const initial = fixture.events.map((ev) => {
    if (ev.type !== "escalation_required") return ev;
    return {
      ...ev,
      payload: {
        ...(ev.payload ?? {}),
        browser_login: true,
        question: "请在浏览器完成登录后再继续。",
      },
    };
  });
  return {
    name: "browser_login_escalate",
    kind: "hot_gate",
    initial,
    continueSameStream: [],
    resumeStream: [],
  };
}

const PLANS: Record<string, () => ScriptPlan> = {
  single_agent_text: () => complete("single_agent_text"),
  multi_agent_delegate: () => complete("multi_agent_delegate"),
  multi_agent_debate: () => complete("multi_agent_debate"),
  browser_activity_card: () => browserActivityCard(),
  browser_login_escalate: () => browserLoginEscalate(),
  approval_resolved_continue: () =>
    splitHot(loadFixture("approval_resolved_continue")),
  team_preview_resolved_continue: () => splitColdTeamPreview(),
};

/** Parse `__e2e_script__:<name>` from the user message; default single_agent_text. */
export function resolveScriptName(content: string): string {
  const m = /__e2e_script__:([a-z0-9_]+)/i.exec(content);
  const name = m?.[1] ?? "single_agent_text";
  if (!(name in PLANS)) {
    throw new Error(
      `Unknown e2e script "${name}". Known: ${Object.keys(PLANS).join(", ")}`,
    );
  }
  return name;
}

export function buildPlan(scriptName: string): ScriptPlan {
  const factory = PLANS[scriptName];
  if (!factory) throw new Error(`Unknown script ${scriptName}`);
  return factory();
}

/** Sanity: cold/hot scripts must have a gate event in the initial segment. */
export function assertPlanHasBoundary(plan: ScriptPlan): void {
  if (plan.kind === "complete") return;
  const types = new Set(plan.initial.map((e) => e.type));
  const ok =
    plan.kind === "hot_gate"
      ? [...HOT_REQUIRED].some((t) => types.has(t))
      : [...COLD_REQUIRED].some((t) => types.has(t));
  if (!ok) {
    throw new Error(`Script ${plan.name} initial segment missing gate event`);
  }
}
