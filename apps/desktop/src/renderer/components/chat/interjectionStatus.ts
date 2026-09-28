import type { UserInterjectionStatus } from "@/stores/execution";

/**
 * 五态文案（桌面/手机 parity，逐字）。心智：只对主 Agent 说话。
 *
 * `turnTerminal`：纯前端派生态——回合已收口而协议 status 仍为 `received` 时，
 * 不得再写「等待读取」；不新增协议 status 枚举。
 * `dequeued`：同为派生态——排队项已出队开跑后，「将在下一条回复处理」的未来时已过期。
 * `injected` 不画徽章 / note（新回合的终态之一）。`addressed` 新回合不发；
 * 旧日记回放同样不画徽章。成功结果已在助手续写 / 协作图。
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
 * `injected` / `addressed` 不画徽章 / note。`injected` 是内容进了上下文；
 * `addressed` 只出现在旧日记。成功结果已在助手续写 / 协作图，再贴收据无增量。
 */
export function showInterjectionStatusChrome(
  status: UserInterjectionStatus | string | null | undefined,
): boolean {
  return status !== "addressed" && status !== "injected";
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
  // 已看到：primary 蓝（非成功绿）
  injected: "border-primary/35 bg-primary/10 text-primary",
  // 纳入合成：实心底+正文色（克制收束，勿假绿）
  addressed: "border-border bg-muted text-foreground",
  // 排队：描边空心，与「等待读取」区分
  queued: "border-border/60 bg-transparent text-muted-foreground",
  // 已送达待读：浅底静默
  received: "border-border/40 bg-muted/40 text-muted-foreground",
};
