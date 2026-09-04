/**
 * Face 徽标预算（批 R3 决策 3）——治「节点 face 功能过载」。
 *
 * 原则（协作图架构重设计提案 §二 决策 3）：face 三层「角色 → 在干什么 → 用时」是核心，
 * 恒显；其上叠加的**功能徽标**同屏 ≤2 枚，按优先级 **待拍板 > 异常 > 过程性** 择取，
 * 被挤出的（多为过程性）标记归 hover peek 与 run 详情——而 aria 播报恒含全部状态
 * （a11y 不因视觉降级而回归）。
 *
 * 身份层：辩论立场走阵营色 + 头像字（默认不写正方/反方字标）；辩论轮次角标
 * 仍恒显、不计预算。
 *
 * 纯函数、无 React——{@link buildAgentNodePresentation} 调 {@link pickFaceBadges}
 * 得可见键集合，`AgentNodeFace` 据此 gate 每枚徽标；单测直接钉选择逻辑。
 */

/** 三优先级桶：待拍板（decision）> 异常（anomaly）> 过程性（process）。 */
export type FaceBadgeBucket = "decision" | "anomaly" | "process";

/** face 上受预算约束的功能徽标键（辩论轮次角标不在内；立场不再占字标）。 */
export type FaceBadgeKey =
  | "escalation"
  | "checkpoint"
  | "reviewConcern"
  | "revision"
  | "handoff"
  | "crossExam";

export interface FaceBadgeDescriptor {
  key: FaceBadgeKey;
  bucket: FaceBadgeBucket;
}

/** face 同屏功能徽标上限（决策 3：≤2 枚）。 */
export const FACE_BADGE_BUDGET = 2;

const BUCKET_RANK: Record<FaceBadgeBucket, number> = {
  decision: 0,
  anomaly: 1,
  process: 2,
};

/**
 * 输入信号——由 {@link buildAgentNodePresentation} 从 run 态归一后传入，
 * 与 face 的具体渲染解耦，故预算逻辑可脱离整份 presentation 单测。
 */
export interface FaceBadgeSignals {
  /** 待你拍板的阻塞升级数（>0 → 待拍板桶）。 */
  escalationPending: number;
  /** 已上报但非待拍板的升级数（scope/dep 才在 face 留 passive 标记 → 过程性）。 */
  escalationRaised: number;
  escalationKind:
    | "normal"
    | "scope"
    | "dep"
    | "contradiction"
    | null
    | undefined;
  /** 检查点待放行（待拍板桶）。 */
  checkpointPending: boolean;
  /** 检查点已停止（异常桶）。 */
  checkpointStopped: boolean;
  /** 检查点已放行/已调整（过程性桶；与 pending/stop 互斥）。 */
  checkpointReleased: boolean;
  /** 方向风险 / 待关注（异常桶）。 */
  reviewConcern: boolean;
  /** 同人接续「续 ×N」角标（过程性；辩论轮次角标不走此路，属身份层）。 */
  revision: boolean;
  /** 回落换人「接手」角标（过程性；仅在无 revision 角标时占右上角）。 */
  handoff: boolean;
  /** 收场「含质询」后缀标记（过程性；质询作答失败走状态行不计此）。 */
  crossExamSuffix: boolean;
}

/**
 * 盘点当前 face 上**存在**的功能徽标，按「同桶内的展示优先序」返回描述符。
 * 顺序即同桶内择取的先后（越靠前越优先保留）。
 */
export function buildFaceBadgeDescriptors(
  s: FaceBadgeSignals,
): FaceBadgeDescriptor[] {
  const out: FaceBadgeDescriptor[] = [];
  // 待你拍板（decision）与「已上报」passive 标记（process）互斥，共用 escalation 键。
  if (s.escalationPending > 0) {
    out.push({ key: "escalation", bucket: "decision" });
  } else if (
    s.escalationRaised > 0 &&
    (s.escalationKind === "scope" ||
      s.escalationKind === "dep" ||
      s.escalationKind === "contradiction")
  ) {
    out.push({ key: "escalation", bucket: "process" });
  }
  // 检查点：待放行=待拍板；已停止=异常；已放行/已调整=过程性（三者互斥，共用 checkpoint 键）。
  if (s.checkpointPending) {
    out.push({ key: "checkpoint", bucket: "decision" });
  } else if (s.checkpointStopped) {
    out.push({ key: "checkpoint", bucket: "anomaly" });
  } else if (s.checkpointReleased) {
    out.push({ key: "checkpoint", bucket: "process" });
  }
  if (s.reviewConcern) out.push({ key: "reviewConcern", bucket: "anomaly" });
  // 右上角只占一枚：revision（续 ×N）优先于 handoff（接手），与既有互斥渲染一致。
  if (s.revision) out.push({ key: "revision", bucket: "process" });
  else if (s.handoff) out.push({ key: "handoff", bucket: "process" });
  if (s.crossExamSuffix) out.push({ key: "crossExam", bucket: "process" });
  return out;
}

/**
 * 从存在的功能徽标中按 **待拍板 > 异常 > 过程性** 择取至多 `budget` 枚，返回可见键集合。
 * 稳定排序：先按桶优先级，同桶按传入顺序，故结果可预测（进单测）。
 */
export function pickFaceBadges(
  present: readonly FaceBadgeDescriptor[],
  budget: number = FACE_BADGE_BUDGET,
): Set<FaceBadgeKey> {
  const ordered = present
    .map((b, i) => ({ b, i }))
    .sort(
      (a, z) => BUCKET_RANK[a.b.bucket] - BUCKET_RANK[z.b.bucket] || a.i - z.i,
    )
    .slice(0, Math.max(0, budget))
    .map((x) => x.b.key);
  return new Set(ordered);
}

/** 便捷：直接由信号得可见键集合（presentation 的调用点）。 */
export function visibleFaceBadgeKeys(
  s: FaceBadgeSignals,
  budget: number = FACE_BADGE_BUDGET,
): Set<FaceBadgeKey> {
  return pickFaceBadges(buildFaceBadgeDescriptors(s), budget);
}
