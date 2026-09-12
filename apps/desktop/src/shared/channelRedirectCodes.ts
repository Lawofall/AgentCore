/**
 * Wrong-tool-channel codes shared by renderer fold and main-process outbox.
 * Must stay equal to ``agentcore.runtime.engine.tool_channel_redirect.CHANNEL_REDIRECT_CODES``.
 */
export const CHANNEL_REDIRECT_CODE_LIST = [
  "source_grep_redirect",
  "source_dump_redirect",
  // journal-only; unified `run` no longer emits
  "project_verify_redirect",
  "long_running_redirect",
  "not_a_web_url",
  "url_not_workspace_path",
  "loopback_host",
] as const;

export type ChannelRedirectCode = (typeof CHANNEL_REDIRECT_CODE_LIST)[number];

export const CHANNEL_REDIRECT_CODES: ReadonlySet<string> = new Set(
  CHANNEL_REDIRECT_CODE_LIST,
);
