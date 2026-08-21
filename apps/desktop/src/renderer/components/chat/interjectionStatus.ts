import type { UserInterjectionStatus } from "@/stores/execution";

/**
 * 五态文案（桌面/手机 parity，逐字）。心智：只对主 Agent 说话。
 *
 * `turnTerminal`：纯前端派生态——回合已收口而协议 status 仍为 `received` 时，
 * 不得再写「等待读取」；不新增协议 status 枚举。
 * `dequeued`：同为派生态——排队项已出队开跑后，「将在下一条回复处理」的未来时已过期。
 * `addressed` 文案仍映射（协议态），但 {@link showInterjectionStatusChrome} 为 false：
 * 结果已在图/回复里，徽章与图内 note 不画。
 */
export function interjectionStatusLabel(
  status: UserInterjectionStatus | string | null | undefined,
  opts?: { turnTerminal?: boolean; dequeued?: boolean },
): string {
  switch (status) {
    case "queued":
      return opts?.dequeued ? "已转入下一回合" : "将在下一条回复处理";
    case "failed":
      return "未被处理";
    case "addressed":
      return "已纳入本回合合成";
    case "injected":
      return "主 Agent 已看到";
    case "received":
      return opts?.turnTerminal
        ? "未被主 Agent 读取"
        : "已送达，等待主 Agent 读取";
    default:
      return opts?.turnTerminal
        ? "未被主 Agent 读取"
        : "已送达，等待主 Agent 读取";
  }
}

/**
 * 拥有该插话的回合是否已无法再「读取」received。
 * in-flight（streaming/stopping/preflight）且气泡仍 isStreaming → 未收口；
 * terminal / idle / 历史气泡 → 已收口。
 */
export function isInterjectionTurnTerminal(
  turnPhase: string,
  messageIsStreaming: boolean | null | undefined,
): boolean {
  if (
    turnPhase === "stopped" ||
    turnPhase === "completed" ||
    turnPhase === "failed"
  ) {
    return true;
  }
  if (
    turnPhase === "streaming" ||
    turnPhase === "stopping" ||
    turnPhase === "preflight"
  ) {
    return messageIsStreaming !== true;
  }
  return true;
}

/**
 * `addressed` 不画徽章 / note。协议仍发该态（清 pending、避免升格排队）；
 * 图内处置结果已在协作图与助手回复里，再贴收据无增量。
 */
export function showInterjectionStatusChrome(
  status: UserInterjectionStatus | string | null | undefined,
): boolean {
  return status !== "addressed";
}

export type InterjectionStatusTone =
  | "received"
  | "injected"
  | "queued"
  | "failed"
  | "addressed";

/** Visual tone — addressed 勿假绿成功；五态拉开层次。 */
export function interjectionStatusTone(
  status: UserInterjectionStatus | string | null | undefined,
): InterjectionStatusTone {
  if (status === "queued") return "queued";
  if (status === "failed") return "failed";
  if (status === "addressed") return "addressed";
  if (status === "injected") return "injected";
  return "received";
}

export const INTERJECTION_TONE_CLASS: Record<InterjectionStatusTone, string> = {
  // 失败：唯一红
  failed: "border-destructive/40 bg-destructive/10 text-destructive",
  // 已看到：品牌蓝（非成功绿）
  injected: "border-primary/35 bg-primary/10 text-primary",
  // 纳入合成：实心底+正文色（克制收束，勿假绿）
  addressed: "border-border bg-muted text-foreground",
  // 排队：描边空心，与「等待读取」区分
  queued: "border-border/60 bg-transparent text-muted-foreground",
  // 已送达待读：浅底静默
  received: "border-border/40 bg-muted/40 text-muted-foreground",
};
