import { formatLocalMoment } from "@/lib/recoveryMoment";
import type { ConversationContextGap } from "@/stores/conversation";

/**
 * 压缩没跟上、早期对话真的掉出窗口时的降级文案（后端 `context_gap` 为准）。
 *
 * 压缩成功走时间线隔断，不占 composer。
 *
 * 照记忆常驻配额卡的诚实性范式：不能读成「AI 从此记不住东西」。缺 `recoveryAt`
 * 时只说会自动重试，绝不自行编造一个恢复时间——不知道就说不知道。恢复时刻是后端下发的
 * 绝对瞬间，按用户本机时区成文（不标时区名，屏幕上的钟就是他自己的）。
 *
 * 返回 `null` = 没有可诚实陈述的损失，什么都不显示。
 */
export function composerContextGapHint(
  gap: ConversationContextGap | undefined,
): string | null {
  const dropped = gap?.droppedMessages ?? 0;
  if (dropped < 1) return null;
  const moment = formatLocalMoment(gap?.recoveryAt);
  const relief = moment
    ? `上游额度将于 ${moment} 恢复，届时自动补上`
    : "系统会自动重试补上";
  return `较早对话没能收入摘要，本轮 AI 读不到最早的 ${dropped} 条（原文仍在）。${relief}；急用把要点再说一遍。`;
}
