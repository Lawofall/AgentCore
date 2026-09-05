/**
 * Wrong-tool-channel steer — byte-equal to
 * `agentcore.runtime.engine.tool_channel_redirect.CHANNEL_REDIRECT_CODES`.
 *
 * Wire `tool_use_end.status` is `redirect` (not `error`). Old journals stored
 * these as `status=error` + a redirect `failure.code`; fold normalizes them.
 */

export const CHANNEL_REDIRECT_CODES = new Set([
  "source_grep_redirect",
  "source_dump_redirect",
  "project_verify_redirect", // journal-only; unified `run` no longer emits
  "long_running_redirect",
  "not_a_web_url",
  "url_not_workspace_path",
  "loopback_host",
]);

/** Collapsed process-row title. Destination `toolName` supplies the icon. */
export const CHANNEL_REDIRECT_FACE: Record<
  string,
  { label: string; toolName: string }
> = {
  source_grep_redirect: { label: "改用搜索", toolName: "grep" },
  source_dump_redirect: { label: "改用读文件", toolName: "file_read" },
  long_running_redirect: { label: "改用终端", toolName: "terminal" },
  not_a_web_url: { label: "改用读文件", toolName: "file_read" },
  url_not_workspace_path: { label: "改用读网页", toolName: "web_fetch" },
  loopback_host: { label: "改用本机查看", toolName: "browser" },
};

export function isChannelRedirectCode(
  code: string | undefined | null,
): boolean {
  return !!code && CHANNEL_REDIRECT_CODES.has(code);
}

export function channelRedirectFace(code: string | undefined | null): {
  label: string;
  toolName: string;
} | null {
  if (!code) return null;
  return CHANNEL_REDIRECT_FACE[code] ?? null;
}

export type ToolWireStatus = "running" | "success" | "error" | "redirect";

/** Live `redirect` plus journal compat (`error` + redirect code). */
export function resolveToolWireStatus(
  status: string | undefined,
  failure?: { code?: string } | null,
): ToolWireStatus {
  if (status === "running") return "running";
  if (status === "redirect") return "redirect";
  if (status === "error" && isChannelRedirectCode(failure?.code)) {
    return "redirect";
  }
  if (status === "error") return "error";
  return "success";
}

/** `tool_use_end` never stays running. */
export function resolveToolEndStatus(
  status: string | undefined,
  failure?: { code?: string } | null,
): "success" | "error" | "redirect" {
  const wire = resolveToolWireStatus(status, failure);
  return wire === "running" ? "success" : wire;
}
