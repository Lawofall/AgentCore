/**
 * 设置 · 通用「发送方式」：Enter 发送（默认）或 Enter 换行。
 * Ctrl/Cmd+Enter 始终是提交和弦（生成中的插队语义由调用方决定）。
 */

export type ComposerSubmitKind = "mod" | "bare";

export function isComposerSubmitKey(
  e: {
    key: string;
    shiftKey: boolean;
    ctrlKey: boolean;
    metaKey: boolean;
  },
  sendOnEnter: boolean,
): ComposerSubmitKind | null {
  if (e.key !== "Enter") return null;
  if (e.ctrlKey || e.metaKey) return "mod";
  if (e.shiftKey) return null;
  return sendOnEnter ? "bare" : null;
}

/** 队还在 / 工具步还在时，发送钮上的排队 vs 插队说明。 */
export function occupiedQueueHint(sendOnEnter: boolean): string {
  return sendOnEnter
    ? "排队至本回合结束后发送（Enter）；Ctrl/Cmd+Enter 送进当前回合"
    : "排队至本回合结束后发送；Ctrl/Cmd+Enter 送进当前回合";
}
