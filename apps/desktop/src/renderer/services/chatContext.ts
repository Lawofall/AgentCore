import { api } from "@/services/api";
import type { SidecarHistoryEntry } from "@shared/sidecar-contract";

/** Sidecar / 桌面拉窗失败时同一句用户可见说明（勿空窗开跑）。 */
export const CHAT_CONTEXT_UNAVAILABLE_MESSAGE =
  "未能加载对话历史，请稍后重试。";

/**
 * 服务端统一装配的 CEO 窗口（摘要 + 近窗 + harvest 注记）。
 * 与 sidecar account 窄票打的是同一条 ``POST /v1/account/conversations/chat-context``。
 * 响应缺 ``history`` 数组视为拉窗失败，不把它当成「新会话空窗」。
 */
export async function fetchChatContext(
  conversationId: string,
): Promise<SidecarHistoryEntry[]> {
  const res = await api.post<{ history?: SidecarHistoryEntry[] }>(
    "/v1/account/conversations/chat-context",
    { conversation_id: conversationId },
  );
  const rows = res?.history;
  if (!Array.isArray(rows)) {
    throw new Error(CHAT_CONTEXT_UNAVAILABLE_MESSAGE);
  }
  return rows.filter(
    (row): row is SidecarHistoryEntry =>
      (row.role === "user" || row.role === "assistant") &&
      typeof row.content === "string",
  );
}
