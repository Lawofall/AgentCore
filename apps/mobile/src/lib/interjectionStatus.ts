/**
 * S2 五态文案（经典 + 协调共用；两端 parity 逐字一致）。心智：只对主 Agent 说话。
 * `addressed` 文案仍映射，但 {@link showInterjectionStatusChrome} 为 false（不画徽章/note）。
 */
export function interjectionStatusLabel(
  status: string | null | undefined,
  opts?: { turnClosed?: boolean },
): string {
  switch (status) {
    case "queued":
      return "将在下一条回复处理";
    case "failed":
      return "未被处理";
    case "addressed":
      return "已纳入本回合合成";
    case "injected":
      return "主 Agent 已看到";
    case "received":
      return opts?.turnClosed
        ? "未被主 Agent 读取"
        : "已送达，等待主 Agent 读取";
    default:
      return opts?.turnClosed
        ? "未被主 Agent 读取"
        : "已送达，等待主 Agent 读取";
  }
}

/**
 * `addressed` 不画徽章 / note。协议仍发该态（清 pending、避免升格排队）；
 * 图内处置结果已在协作图与助手回复里，再贴收据无增量。
 */
export function showInterjectionStatusChrome(
  status: string | null | undefined,
): boolean {
  return status !== "addressed";
}

export type InterjectionStatusTone =
  | "received"
  | "injected"
  | "queued"
  | "failed"
  | "addressed";

/** Visual tone — 五态可区分但克制；addressed 勿假绿成功。 */
export function interjectionStatusTone(
  status: string | null | undefined,
): InterjectionStatusTone {
  if (status === "queued") return "queued";
  if (status === "failed") return "failed";
  if (status === "addressed") return "addressed";
  if (status === "injected") return "injected";
  return "received";
}
