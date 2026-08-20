import type { ResumeDeferredBusyReason } from "@/lib/resumeDeferred";
import type { ResumeSettledTurnStatus } from "@/lib/resumeSettled";
import type { ResumeOrigin } from "@/stores/pausedTurns";
import type { InteractionStatus } from "@/types/interactionExt";
import {
  INTERACTION_ID_FIELD,
  type InteractionKind,
  isHotInteractionKind,
} from "./registry";

// Re-export registry surface so existing `@/stores/interactions` imports keep working.
export {
  COLD_RESUME_KINDS,
  HOT_GATE_INTERACTION_KINDS,
  HOT_INTERACTION_KINDS,
  INTERACTION_CARD_NAME,
  INTERACTION_ID_FIELD,
  INTERACTION_SUBMIT_PATH,
  STAGE_INTERACTION_KINDS,
  hotGateKindTitle,
  isColdResumeKind,
  isHotGateInteractionKind,
  isHotInteractionKind,
  isStageInteractionKind,
  kindFromRequiredEvent,
  kindFromResolvedEvent,
  type ColdResumeKind,
  type HotGateInteractionKind,
  type InteractionKind,
  type InteractionSubmitPath,
  type StageInteractionKind,
} from "./registry";

/** One user-facing interaction card in the unified store (方案 §3.2). */
export interface InteractionEntry {
  id: string;
  kind: InteractionKind;
  status: InteractionStatus;
  conversationId: string;
  messageId: string;
  /** Original `*_required` wire payload. */
  payload: Record<string, unknown>;
  /** Settlement payload when status is resolved (kind-specific). */
  resolution?: Record<string, unknown>;
  /**
   * Live transport that delivered this entry (SSE `ctx.source`).
   * Cold submit prefers this for sidecar vs server routing; pausedTurns remains
   * recovery/`setForConversation` shell + origin fallback.
   */
  origin?: ResumeOrigin;
  /**
   * 这张卡进入本地时的观察序号（与 {@link beginPausedSnapshot} 同一条）。
   * `/recovery` 只对发起前就浮现、且来源已确认的卡有处置权。
   */
  surfacedSeq?: number;
  /**
   * Cold resume accepted while slot busy (EPHEMERAL `resume_deferred`).
   * Settlement is locked — UI keeps submitting and hides cancel-改口.
   */
  resumeDeferred?: { busyReason: ResumeDeferredBusyReason };
  /**
   * 冷卡「继续」落在一张已被上一次续跑吃掉的帧上（EPHEMERAL `resume_settled`）。
   *
   * 与 {@link settledByReceipt} 的差别在于**能说出多少**：这一帧带着 journal 里那条
   * settlement 的事实（决策 / 落定时刻 / 回合当前状态），所以卡可以收成一句说得清的
   * 结果态，而不只是「已经结了」。仍然说不出的是**谁**——线材里没有处理方，不认领归属。
   */
  resumeSettled?: {
    decision: string;
    decidedAt: string;
    turnStatus: ResumeSettledTurnStatus;
  };
  /**
   * 这张卡是**另一端**（手机 / 另一台桌面）拍板收口的（云对话多端同权 B2 · 验收 2）。
   *
   * 线材里没有「谁答的」，所以判定只在本会话内成立：本端一直显示着 pending（从未
   * `beginSubmit`）却收到一帧 live `*_resolved`、且这一帧确实**是人答的** ⇒ 不是我点的。
   * 重放段不算（那是历史回放，不是刚发生的转折），journal 水合也不算（上一次会话可能正是
   * 本端点的），CEO 裁决 / 假设推进 / 超时也不算（压根没有人）。
   *
   * 已知窄边角：用户点「允许本回合」后，服务端会顺手放行同类的兄弟卡；若某张兄弟卡的
   * `*_required` 恰好在这一点之后才到达本端（毫秒级），本端来不及乐观收它，就会把服务端
   * 的顺带放行读成另一端。只影响一条几秒后退场的提示，不改任何执行语义。
   *
   * 用途只有一个：卡不能**直接消失**——消失会让用户以为是自己点的。
   */
  settledElsewhere?: boolean;
  /**
   * 本端点下去，服务端回执说**这张卡已经结了**（`already_processed` / 404）。
   *
   * 与 {@link settledElsewhere} 是两回事：回执只说「已经结了」，不带 `status` /
   * `arbitrated_by`，证不了是谁结的——升级卡的 404 可能是主管接管仲裁，也可能是超时兜底。
   * 所以这条只用来**关掉操作面**（卡不能再点，否则用户一点再点、次次 404），归属仍等带线材
   * 字段的 `*_resolved` 帧来证；那帧到之前 `resolution` 是空的，呈现侧据此说「已处理」而不是
   * 替它猜一个「已批准」。
   *
   * 也不同于 `orphaned`：orphaned 是卡作废了（回合结束 / 服务重启），这里是卡**被答了**。
   */
  settledByReceipt?: boolean;
}

/**
 * 「等你」判定（侧栏灯 / 全局提醒共用语义）：这条交互是否正把执行阻塞在用户身上。
 *
 * - 热阻塞 kind（`INTERACTION_KIND_WIRE.hot`）pending 或 submitting 时为真——
 *   live turn 挂在卡上等答复。
 * - escalation 例外：`awaiting === "ceo"` 由 CEO 仲裁，用户无需行动 → 不算。
 * - 冷 kind（`pausesTurn && !hot`）恒为假：可操作权威是 InteractionStore cold
 *   pending（ResumePrompt）；侧栏灯由调用方另订 pausedTurns recovery 壳或 cold
 *   pending，不经本函数。
 * - stage（`stage_card`）团队没停 → 不算。
 */
export function isAwaitingUserEntry(entry: InteractionEntry): boolean {
  if (entry.status !== "pending" && entry.status !== "submitting") return false;
  if (!isHotInteractionKind(entry.kind)) return false;
  if (entry.kind === "escalation" && entry.payload.awaiting === "ceo") {
    return false;
  }
  return true;
}

export function idFromRequiredPayload(
  kind: InteractionKind,
  payload: Record<string, unknown>,
): string | null {
  const field = INTERACTION_ID_FIELD[kind];
  const raw = payload[field];
  return typeof raw === "string" && raw.length > 0 ? raw : null;
}

export function idFromResolvedPayload(
  kind: InteractionKind,
  payload: Record<string, unknown>,
): string | null {
  return idFromRequiredPayload(kind, payload);
}
