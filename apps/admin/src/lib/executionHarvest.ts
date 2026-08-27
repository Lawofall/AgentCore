/**
 * 异步团队收口：系统合成的「用户」消息（usage.origin=execution_harvest）。
 * 管理复盘需可见归因，不能当成老板手打；正文前缀为旧数据兜底。
 */

export const EXECUTION_HARVEST_ORIGIN = "execution_harvest";

/** 后端落库的合成用户消息正文前缀（history.py `_HARVEST_USER_PREFIX`）。 */
export const HARVEST_USER_CONTENT_PREFIX = "【系统收口】";

export function isExecutionHarvestMessage(msg: {
  role: string;
  content?: string | null;
  origin?: string | null;
}): boolean {
  if (msg.role !== "user") return false;
  if (msg.origin === EXECUTION_HARVEST_ORIGIN) return true;
  return (msg.content ?? "").startsWith(HARVEST_USER_CONTENT_PREFIX);
}

/** harvest_kind → 运维可读短标签；未知 kind 回落正文启发式。 */
export function harvestKindLabel(
  kind: string | null | undefined,
  content?: string | null,
): string | null {
  switch (kind) {
    case "success":
      return "已完成";
    case "failure":
      return "有失败";
    case "cancelled":
      return "已取消";
    default:
      break;
  }
  const text = content ?? "";
  if (text.includes("已取消") || text.includes("中断")) return "已取消";
  if (text.includes("队员失败")) return "有失败";
  if (text.includes("全部完成")) return "已完成";
  return null;
}
