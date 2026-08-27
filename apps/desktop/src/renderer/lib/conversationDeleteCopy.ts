import { notifyInfo } from "@/lib/toast";

/**
 * One source of truth for what deleting a conversation is called and what it
 * promises, shared by every row that offers it (sidebar, 全部对话 list, 已归档 list,
 * 文件 hub rail, bulk bar).
 *
 * Delete is one-click soft-delete. Safety net is this toast's 撤销 plus「最近删除」.
 * Do not invent a confirm label or a "permanent / irreversible" promise at a call
 * site — that was never true of `DELETE /v1/conversations/{id}`.
 */

/** Menu item / icon label for deleting a conversation. */
export const DELETE_CONVERSATION_LABEL = "删除对话";

/**
 * The post-delete toast. 撤销 is how most people come back from a mistaken delete —
 * they notice within seconds and never open「最近删除」— so every delete offers it,
 * and the row that raises this has usually unmounted by the time it is clicked (the
 * undo therefore belongs to a hook-level mutation, not a per-call callback).
 */
export function notifyConversationDeleted(
  title: string,
  onUndo: () => void,
): void {
  notifyInfo("已删除对话", {
    description: title,
    duration: 8000,
    action: { label: "撤销", onClick: onUndo },
  });
}
