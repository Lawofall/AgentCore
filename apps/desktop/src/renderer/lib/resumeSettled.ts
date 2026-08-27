/**
 * 冷卡「继续」落在一张**已经被处理过**的挂起帧上 — EPHEMERAL `resume_settled`
 * （三模型 · 幂等成功）。
 *
 * 服务端不再对「已处理」回 404：帧被上一次续跑吃掉时回 200 + 本帧，说清那次决策是什么、
 * 什么时候落的、回合现在到哪一步了。`turn_status=running` 表示同一条连接紧接着就是那次
 * 续跑的实时流——用户点了「继续」，AI 正在继续，他就该无缝看着它继续。
 *
 * 结算结论的权威在服务端 `paused_turn_outcomes`——消费掉这张卡的那一方在同一个事务里写下的
 * 决策、时刻与 checkpoint。结算方也记在那行上，但**不上线**：本帧只说「何时以什么决策结的」，
 * 不替任何人认领。
 */

export type ResumeSettledCardKind = "ask_user" | "plan_review";

export type ResumeSettledTurnStatus =
  | "running"
  | "complete"
  | "incomplete"
  | "failed"
  | "unknown";

export interface ResumeSettledPayload {
  message_id: string;
  conversation_id: string;
  kind: ResumeSettledCardKind;
  checkpoint_id: string;
  decision: string;
  decided_at: string;
  turn_status: ResumeSettledTurnStatus;
}

const CARD_KINDS: readonly string[] = ["ask_user", "plan_review"];

const TURN_STATUSES: readonly string[] = [
  "running",
  "complete",
  "incomplete",
  "failed",
  "unknown",
];

/** 决策取值来自 journal 里那条 `*_resolved`，未知值原样透出（不猜、不吞）。 */
const DECISION_LABEL: Record<string, string> = {
  continue: "继续",
  adjust: "调整",
  stop: "停止",
  research_first: "先调研",
  use_assumption: "按假设继续",
  logged_in: "已登录，继续",
  timeout: "超时自动推进",
};

export function parseResumeSettledPayload(
  payload: unknown,
): ResumeSettledPayload | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;
  const messageId = p.message_id;
  const conversationId = p.conversation_id;
  const kind = p.kind;
  const checkpointId = p.checkpoint_id;
  if (typeof messageId !== "string" || !messageId) return null;
  if (typeof conversationId !== "string" || !conversationId) return null;
  if (typeof kind !== "string" || !CARD_KINDS.includes(kind)) return null;
  if (typeof checkpointId !== "string" || !checkpointId) return null;
  // checkpoint_id 由结算表的 CHECK 约束保底（空值写不进去），上面那道只是必填字段的解析校验。
  // decision / decided_at 仍按可缺处理——不因此丢掉整帧，文案会自动省掉说不出口的那半句。
  // turn_status 认不出的取值按「不知道」处理，只有 `running` 才代表「后面还有流」，
  // 所以降级永远偏保守。
  return {
    message_id: messageId,
    conversation_id: conversationId,
    kind: kind as ResumeSettledCardKind,
    checkpoint_id: checkpointId,
    decision: typeof p.decision === "string" ? p.decision : "",
    decided_at: typeof p.decided_at === "string" ? p.decided_at : "",
    turn_status:
      typeof p.turn_status === "string" && TURN_STATUSES.includes(p.turn_status)
        ? (p.turn_status as ResumeSettledTurnStatus)
        : "unknown",
  };
}

export function resumeSettledDecisionLabel(decision: string): string {
  const raw = decision.trim();
  if (!raw) return "";
  return DECISION_LABEL[raw] ?? raw;
}

/** `decided_at` 是 journal 那条的时间戳；认不出就不说时间，别编一个。 */
function formatDecidedAt(decidedAt: string): string {
  if (!decidedAt.trim()) return "";
  const at = new Date(decidedAt);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * 结果态卡面主句：这张卡**何时**以**什么决策**结的。
 *
 * 两半都可能说不出口（旧帧 / journal 里没带），缺哪半就少说哪半——宁可短，不许编。
 */
export function resumeSettledHeadline(facts: {
  decision: string;
  decidedAt: string;
}): string {
  const when = formatDecidedAt(facts.decidedAt);
  const decision = resumeSettledDecisionLabel(facts.decision);
  if (when && decision) return `已在 ${when} 以「${decision}」处理`;
  if (decision) return `已以「${decision}」处理`;
  if (when) return `已在 ${when} 处理`;
  return "已经处理过了";
}

/**
 * 结果态卡面副句：这次回合现在在哪。
 *
 * 全是**信息态**——「别人先处理了」对用户不是故障，`failed` 也一样：那是那次续跑的结局，
 * 不是他这一下点出了问题。红色告警是系统故障的视觉语言，这里一个字都不该用。
 */
export function resumeSettledTurnCopy(
  turnStatus: ResumeSettledTurnStatus,
): string {
  switch (turnStatus) {
    case "running":
      return "AI 正在继续，这里无需再操作。";
    case "complete":
      return "这次回合已经跑完，这里无需再操作。";
    case "incomplete":
      return "这次回合没有跑完，可在上方回复里查看它停在哪。";
    case "failed":
      return "这次回合以失败收场，可在上方回复里查看原因。";
    default:
      return "这里无需再操作。";
  }
}
