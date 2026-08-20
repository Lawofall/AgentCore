/**
 * 协作图「模式能力表」——按幕 kind / 图级 planType 声明图上具备哪些能力。
 *
 * 禁止在 GraphView / InlineTeamGraph / RunDetailBody 等处再散落
 * `planType === "multi_agent"` / `kind === "debate"` 之类等值判断；一律查本表。
 * 辩论回合与 multi_agent 共享审计注入（修历史漏判 bug），其余能力按行差异化。
 *
 * 批 A1：幕级 {@link actCapabilities}；图级 {@link planCapabilities} 为兼容快捷。
 * 批 A3：节点级能力按所属幕 kind 取 {@link runActCapabilities}；图级取用点改
 * {@link executionGraphCapabilities}（各幕并集）。禁止再按整图 planType 决定
 * runRedirect / forceExpandDebateUnits 等节点行为。
 *
 * → 设计说明见 `docs/04-前端/前端UX设计.md` §5.1。
 */
import type { ActKind, Execution, ExecutionAct } from "@/stores/execution";

export type PlanType = Execution["planType"];
export type { ActKind };

/** 节点修订角标语义（实际文案仍由 run 级 beat / revision 派生）。 */
export type RevisionBadgeStyle = "none" | "hotfix" | "debate";

export interface PlanGraphCapabilities {
  /** 聊天内嵌 / 全屏是否渲染团队协作图（单 Agent 为 false）。 */
  showsTeamGraph: boolean;
  /** 拉取 turn audit 并在图上画 inject 叠加（审计数据流）。 */
  auditInject: boolean;
  /**
   * 辩论主持人子树在 fold 中强制展开（不可收成单节点）。
   * 由 `helpers.computeGraphFold` 的 debateUnits 实现；本字段声明意图。
   */
  forceExpandDebateUnits: boolean;
  /** 内嵌 InlineTeamGraph 默认展开（用户仍可手动收起）。 */
  inlineDefaultExpanded: boolean;
  /** 修订角标风格：热修 vN / 辩论 beat / 无。 */
  revisionBadgeStyle: RevisionBadgeStyle;
  /**
   * 进行中 run 详情「改方向」redirect。辩论明确不开放（产品决策：辩手须独立对抗，
   * 中途「场边教练」会污染胜负参照；理由与扩开前置见 前端UX设计.md §5.1）。
   */
  runRedirect: boolean;
}

export const PLAN_GRAPH_CAPABILITIES: Record<PlanType, PlanGraphCapabilities> =
  {
    single_agent: {
      showsTeamGraph: false,
      auditInject: false,
      forceExpandDebateUnits: false,
      inlineDefaultExpanded: false,
      revisionBadgeStyle: "none",
      runRedirect: false,
    },
    multi_agent: {
      showsTeamGraph: true,
      auditInject: true,
      forceExpandDebateUnits: false,
      inlineDefaultExpanded: true,
      revisionBadgeStyle: "hotfix",
      runRedirect: true,
    },
    debate: {
      showsTeamGraph: true,
      auditInject: true,
      forceExpandDebateUnits: true,
      inlineDefaultExpanded: true,
      revisionBadgeStyle: "debate",
      runRedirect: false,
    },
  };

const IDLE: PlanGraphCapabilities = PLAN_GRAPH_CAPABILITIES.single_agent;

/** 幕级能力取用——按幕 kind 查表。 */
export function actCapabilities(
  kind: ActKind | null | undefined,
): PlanGraphCapabilities {
  if (kind == null) return IDLE;
  return PLAN_GRAPH_CAPABILITIES[kind];
}

/** 图级兼容快捷；`null`/`undefined` 回落为单 Agent（无图能力）。单幕图行为等价幕级取用。 */
export function planCapabilities(
  planType: PlanType | null | undefined,
): PlanGraphCapabilities {
  if (planType == null) return IDLE;
  return PLAN_GRAPH_CAPABILITIES[planType];
}

/** 混合图：acts 同时含 multi_agent 与 debate（全屏默认落协作图）。 */
export function isMixedActExecution(
  execution: Pick<Execution, "acts"> | null | undefined,
): boolean {
  const acts = execution?.acts;
  if (!acts || acts.length < 2) return false;
  let hasMa = false;
  let hasDebate = false;
  for (const a of acts) {
    if (a.kind === "multi_agent") hasMa = true;
    if (a.kind === "debate") hasDebate = true;
    if (hasMa && hasDebate) return true;
  }
  return false;
}

/**
 * 全屏回合详情默认 tab（批 A3）。
 * 混合图 → 协作图；纯辩论（含单幕辩论）→ 辩论室；其余 → 协作图。
 * `hasDebateSignal` 由调用方传入（通常 = {@link isDebate}），避免本模块依赖 debate 细节。
 */
export function defaultTurnDetailView(
  execution: Pick<Execution, "acts"> | null | undefined,
  hasDebateSignal: boolean,
): "graph" | "debate" {
  if (!execution) return "graph";
  if (isMixedActExecution(execution)) return "graph";
  if (hasDebateSignal) return "debate";
  return "graph";
}

/** Resolve the act declaration for a run (缺省 act-1 / planType 兼容合成幕). */
export function actForRun(
  execution: Pick<Execution, "acts" | "runs" | "planType">,
  runId: string,
): ExecutionAct | null {
  const run = execution.runs.find((r) => r.id === runId);
  const actId = run?.actId ?? "act-1";
  const fromActs = execution.acts?.find((a) => a.actId === actId);
  if (fromActs) return fromActs;
  if (execution.acts?.length === 1) return execution.acts[0];
  return null;
}

/** 节点所属幕的能力——RunDetailBody / 节点角标等按 run 取用。 */
export function runActCapabilities(
  execution: Pick<Execution, "acts" | "runs" | "planType"> | null | undefined,
  runId: string,
): PlanGraphCapabilities {
  if (!execution) return IDLE;
  const act = actForRun(execution, runId);
  if (act) return actCapabilities(act.kind);
  return planCapabilities(execution.planType);
}

function orCaps(
  a: PlanGraphCapabilities,
  b: PlanGraphCapabilities,
): PlanGraphCapabilities {
  return {
    showsTeamGraph: a.showsTeamGraph || b.showsTeamGraph,
    auditInject: a.auditInject || b.auditInject,
    forceExpandDebateUnits:
      a.forceExpandDebateUnits || b.forceExpandDebateUnits,
    inlineDefaultExpanded: a.inlineDefaultExpanded || b.inlineDefaultExpanded,
    // 图级角标风格：有辩论幕则用 debate，否则保留 hotfix / none。
    revisionBadgeStyle:
      a.revisionBadgeStyle === "debate" || b.revisionBadgeStyle === "debate"
        ? "debate"
        : a.revisionBadgeStyle === "hotfix" || b.revisionBadgeStyle === "hotfix"
          ? "hotfix"
          : "none",
    // runRedirect 是节点级能力；图级并集不用于开 redirect（见 runActCapabilities）。
    runRedirect: a.runRedirect || b.runRedirect,
  };
}

/**
 * 图级能力 = 各幕并集（聊天内嵌 / 全屏取 auditInject·showsTeamGraph 等）。
 * 无 acts 时回落 {@link planCapabilities}(planType)。
 */
export function executionGraphCapabilities(
  execution: Pick<Execution, "acts" | "planType"> | null | undefined,
): PlanGraphCapabilities {
  if (!execution) return IDLE;
  const acts = execution.acts;
  if (!acts || acts.length === 0) {
    return planCapabilities(execution.planType);
  }
  let caps = IDLE;
  for (const a of acts) {
    caps = orCaps(caps, actCapabilities(a.kind));
  }
  return caps;
}
