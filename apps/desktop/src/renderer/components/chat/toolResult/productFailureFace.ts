/**
 * Tool-row product failure face: which `failure.message` sentences are worth
 * showing the user. Collapsed rows stay one line; specific copy lives in the
 * expanded detail. Generic fallback / empty cousins stay hidden everywhere.
 *
 * Byte-equal to the matching live strings in
 * `apps/server/agentcore/runtime/engine/tool_failure_face.py`.
 * ``RETIRED_VERIFY_RESULT_MESSAGE`` is historical only (backend no longer emits it).
 * ``NO_USER_FACE_CODES`` is a twin of the server set: new events omit ``message``;
 * this hides historical journals that still carry a self-heal aside.
 *
 * Path-missing / str_replace user sentences are byte-equal to
 * `apps/server/agentcore/tools/builtin/file_ops/errors.py`.
 */

/** Default unclassified fallback — no cause, no action. */
export const GENERIC_TOOL_FAILURE_MESSAGE =
  "这一步没能完成，我会换个方式继续。";

/** Retired verify-result aside (new events omit the face; hide historical journals). */
export const RETIRED_VERIFY_RESULT_MESSAGE =
  "这次检查跑完了，结果没有通过。我会看报错继续修。";

/** Missing path — generic fallback when the authored sentence is a model receipt. */
export const MISSING_PATH_USER_FACE = "没找到这个路径，我会换个方式继续。";

/** str_replace did not match disk — generic fallback for leaked receipts. */
export const STR_REPLACE_NO_MATCH_USER_FACE =
  "这段内容和文件对不上，我会换个方式改。";

/** Twin of server ``NO_USER_FACE_CODES``. */
export const NO_USER_FACE_CODES = new Set([
  "verify_result",
  "no_frame",
  "postcondition_failed",
  "session_not_found",
  "session_bound_elsewhere",
  "source_dump_redirect",
  "source_grep_redirect",
  "long_running_redirect",
  "not_a_web_url",
  "url_not_workspace_path",
  "loopback_host",
  "verify_contract",
  "run_contract",
  "http_status_error",
  "site_unreachable",
  "read_timeout",
  "too_many_redirects",
  "workspace_io_error",
  "too_large",
  "sandbox_network_unsupported",
  "language_unavailable",
  "launcher_unavailable",
  "cloud_desk_required",
  "invalid_args",
]);

const HIDDEN_TOOL_FAILURE_MESSAGES = new Set([
  GENERIC_TOOL_FAILURE_MESSAGE,
  "这一步没能用上合适的工具，已跳过；我会换个方式继续。",
  "未找到所需资源，请换一种方式继续。",
  "读写工作区文件时出错，这一步没能完成。我会换个方式再试。",
  RETIRED_VERIFY_RESULT_MESSAGE,
  MISSING_PATH_USER_FACE,
  STR_REPLACE_NO_MATCH_USER_FACE,
  "要改的这段在文件里出现了不止一次，我会换个方式锁定。",
]);

/** Product sentences are one short paragraph. Model receipts are multi-line or steer the agent. */
const COMPACT_USER_FACE_MAX = 200;
const MODEL_RECEIPT_MARKERS = [
  "old_string",
  "反复重试",
  "file_read",
  "file_write",
  "str_replace",
  "glob",
  "grep",
];

export function isCompactUserFace(message: string): boolean {
  const text = message.trim();
  if (!text) return false;
  if (text.includes("\n")) return false;
  if (MODEL_RECEIPT_MARKERS.some((marker) => text.includes(marker))) {
    return false;
  }
  return text.length <= COMPACT_USER_FACE_MAX;
}

export function specificToolFailureMessage(data: {
  status: string;
  failure?: { message?: string | null; code?: string | null } | null;
}): string | null {
  if (data.status !== "error") return null;
  const code = data.failure?.code?.trim() ?? "";
  if (code && NO_USER_FACE_CODES.has(code)) return null;
  const message = data.failure?.message?.trim() ?? "";
  if (!message) return null;
  if (HIDDEN_TOOL_FAILURE_MESSAGES.has(message)) return null;
  if (/^没找到 .+，我会换个方式继续。$/.test(message)) return null;
  return message;
}

/** Expanded tool error: one human sentence. Lookup misses stay on the title row. */
export function compactToolFailureFace(data: {
  status: string;
  toolName?: string;
  failure?: { message?: string | null; code?: string | null } | null;
}): string | null {
  if (data.status !== "error") return null;
  const specific = specificToolFailureMessage(data);
  if (specific && isCompactUserFace(specific)) return specific;
  return null;
}
