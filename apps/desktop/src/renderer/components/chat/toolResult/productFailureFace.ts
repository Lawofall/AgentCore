/**
 * Tool-row product failure face: which `failure.message` sentences are worth
 * showing the user. Collapsed rows stay one line; specific copy lives in the
 * expanded detail. Generic fallback / empty cousins stay hidden everywhere.
 *
 * Byte-equal to the matching live strings in
 * `apps/server/agentcore/runtime/engine/tool_failure_face.py`.
 * ``RETIRED_VERIFY_RESULT_MESSAGE`` is historical only (backend no longer emits it).
 */

/** Default unclassified fallback — no cause, no action. */
export const GENERIC_TOOL_FAILURE_MESSAGE =
  "这一步没能完成，我会换个方式继续。";

/** Retired verify-result aside (new events omit the face; hide historical journals). */
export const RETIRED_VERIFY_RESULT_MESSAGE =
  "这次检查跑完了，结果没有通过。我会看报错继续修。";

const HIDDEN_TOOL_FAILURE_MESSAGES = new Set([
  GENERIC_TOOL_FAILURE_MESSAGE,
  "这一步没能用上合适的工具，已跳过；我会换个方式继续。",
  "未找到所需资源，请换一种方式继续。",
  "读写工作区文件时出错，这一步没能完成。我会换个方式再试。",
  RETIRED_VERIFY_RESULT_MESSAGE,
]);

export function specificToolFailureMessage(data: {
  status: string;
  failure?: { message?: string | null } | null;
}): string | null {
  if (data.status !== "error") return null;
  const message = data.failure?.message?.trim() ?? "";
  if (!message) return null;
  if (HIDDEN_TOOL_FAILURE_MESSAGES.has(message)) return null;
  return message;
}
