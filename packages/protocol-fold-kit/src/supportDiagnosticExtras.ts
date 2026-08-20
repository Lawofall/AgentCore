/**
 * 「复制排查包」id / extras 成文 —— 桌面与手机必须逐字同一份。
 *
 * 只拷 allowlist 字段。凭据 id / API key / credential 不进包（用户会贴给客服）。
 * `upstream_body_preview` 再做一轮脱敏与截断；冷重载没 context 是已知限制，不在这里补。
 */

/** Soft cap including the ellipsis. Server preview is already ~500 chars. */
export const SUPPORT_DIAGNOSTIC_PREVIEW_MAX = 500;

export type SupportErrorContext = {
  empty_diagnosis?: string | null;
  body_kind?: string | null;
  base_url?: string | null;
  vendor_code?: string | null;
  model?: string | null;
  profile?: string | null;
  tool_count?: number | null;
  upstream_status?: number | null;
  upstream_body_preview?: string | null;
};

/** Ids + optional extras for a paste-ready「排查包」(bubble / composer / strip). */
export type SupportDiagnosticIds = {
  conversationId?: string | null;
  /** Prefer preceding user bubble when copying from an assistant error/regenerate face. */
  messageId?: string | null;
  userMessageId?: string | null;
  traceId?: string | null;
  executionId?: string | null;
  /** Optional LLM / empty-response extras (written only when non-empty). */
  errorCode?: string | null;
  emptyDiagnosis?: string | null;
  bodyKind?: string | null;
  baseUrl?: string | null;
  vendorCode?: string | null;
  model?: string | null;
  profile?: string | null;
  toolCount?: number | null;
  upstreamStatus?: number | null;
  upstreamBodyPreview?: string | null;
  /** Product default is streaming; pass true for empty-response 排查. */
  stream?: boolean | null;
};

export type SupportDiagnosticErrorExtras = {
  errorCode?: string;
  emptyDiagnosis?: string;
  bodyKind?: string;
  baseUrl?: string;
  vendorCode?: string;
  model?: string;
  profile?: string;
  toolCount?: number;
  upstreamStatus?: number;
  upstreamBodyPreview?: string;
  stream?: true;
};

const QUOTED_SECRET_FIELD =
  /"(api[_-]?key|apikey|credential_id|authorization|secret|password|access_token|refresh_token)"\s*:\s*"(?:\\.|[^"\\])*"\s*,?/gi;

function dropSecretJsonFields(text: string): string {
  let out = text.replace(QUOTED_SECRET_FIELD, "");
  out = out.replace(/,\s*,/g, ",");
  out = out.replace(/\{\s*,/g, "{");
  out = out.replace(/,\s*\}/g, "}");
  return out;
}

/**
 * Flatten + redact secrets + cap. Idempotent for already-sanitized previews.
 * Does not scan user chat text — the input is an upstream error body, not a prompt.
 */
export function sanitizeSupportDiagnosticPreview(raw: string): string {
  let text = raw.replace(/\s+/g, " ").trim();
  if (!text) return "";
  text = dropSecretJsonFields(text);
  text = text.replace(/\bsk-[A-Za-z0-9_-]+\b/g, "[redacted]");
  text = text.replace(/\bBearer\s+\S+/gi, "Bearer [redacted]");
  text = text.replace(
    /\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g,
    "[redacted]",
  );
  if (text.length > SUPPORT_DIAGNOSTIC_PREVIEW_MAX) {
    text = `${text.slice(0, SUPPORT_DIAGNOSTIC_PREVIEW_MAX - 1)}…`;
  }
  return text;
}

function optionalText(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed || undefined;
}

function optionalNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

/**
 * Extras for 排查包 from an assistant bubble error (SSE ErrorContext).
 * ``stream: true`` when empty_diagnosis or LLM_EMPTY_RESPONSE (product default stream).
 */
export function supportDiagnosticExtrasFromError(
  error?: {
    code?: string | null;
    context?: SupportErrorContext | null;
  } | null,
): SupportDiagnosticErrorExtras {
  if (!error) return {};
  const ctx = error.context;
  const errorCode = optionalText(error.code);
  const emptyDiagnosis = optionalText(ctx?.empty_diagnosis);
  const bodyKind = optionalText(ctx?.body_kind);
  const baseUrl = optionalText(ctx?.base_url);
  const vendorCode = optionalText(ctx?.vendor_code);
  const model = optionalText(ctx?.model);
  const profile = optionalText(ctx?.profile);
  const toolCount = optionalNumber(ctx?.tool_count);
  const upstreamStatus = optionalNumber(ctx?.upstream_status);
  const previewRaw = optionalText(ctx?.upstream_body_preview);
  const upstreamBodyPreview = previewRaw
    ? sanitizeSupportDiagnosticPreview(previewRaw)
    : undefined;
  const extras: SupportDiagnosticErrorExtras = {};
  if (errorCode) extras.errorCode = errorCode;
  if (emptyDiagnosis) extras.emptyDiagnosis = emptyDiagnosis;
  if (bodyKind) extras.bodyKind = bodyKind;
  if (baseUrl) extras.baseUrl = baseUrl;
  if (vendorCode) extras.vendorCode = vendorCode;
  if (model) extras.model = model;
  if (profile) extras.profile = profile;
  if (toolCount != null) extras.toolCount = toolCount;
  if (upstreamStatus != null) extras.upstreamStatus = upstreamStatus;
  if (upstreamBodyPreview) extras.upstreamBodyPreview = upstreamBodyPreview;
  if (emptyDiagnosis || errorCode === "LLM_EMPTY_RESPONSE") {
    extras.stream = true;
  }
  return extras;
}

function extraLinesFromIds(ids: SupportDiagnosticIds): string[] {
  const errorCode = optionalText(ids.errorCode);
  const emptyDiagnosis = optionalText(ids.emptyDiagnosis);
  const bodyKind = optionalText(ids.bodyKind);
  const baseUrl = optionalText(ids.baseUrl);
  const vendorCode = optionalText(ids.vendorCode);
  const model = optionalText(ids.model);
  const profile = optionalText(ids.profile);
  const toolCount = optionalNumber(ids.toolCount);
  const upstreamStatus = optionalNumber(ids.upstreamStatus);
  const previewRaw = optionalText(ids.upstreamBodyPreview);
  const upstreamBodyPreview = previewRaw
    ? sanitizeSupportDiagnosticPreview(previewRaw)
    : undefined;

  const extraLines: string[] = [];
  if (errorCode) extraLines.push(`error_code: ${errorCode}`);
  if (emptyDiagnosis) extraLines.push(`empty_diagnosis: ${emptyDiagnosis}`);
  if (bodyKind) extraLines.push(`body_kind: ${bodyKind}`);
  if (baseUrl) extraLines.push(`base_url: ${baseUrl}`);
  if (ids.stream === true) extraLines.push("stream: true");
  if (vendorCode) extraLines.push(`vendor_code: ${vendorCode}`);
  if (model) extraLines.push(`model: ${model}`);
  if (profile) extraLines.push(`profile: ${profile}`);
  if (toolCount != null) extraLines.push(`tool_count: ${toolCount}`);
  if (upstreamStatus != null) extraLines.push(`upstream_status: ${upstreamStatus}`);
  if (upstreamBodyPreview) {
    extraLines.push(`upstream_body_preview: ${upstreamBodyPreview}`);
  }
  return extraLines;
}

/**
 * Format a paste-ready「排查包」for support / Cursor AI log lookup.
 * Lead line triggers conversation-logs workflow; trailing line is log_timeline.py.
 * Requires at least one id; extras append after ids when present — extras alone
 * never produce a pack.
 */
export function formatSupportDiagnosticText(ids: SupportDiagnosticIds): string {
  const conversationId = ids.conversationId?.trim() || "";
  const userMessageId = ids.userMessageId?.trim() || "";
  const messageId = ids.messageId?.trim() || "";
  const traceId = ids.traceId?.trim() || "";
  const executionId = ids.executionId?.trim() || "";

  const idLines: string[] = [];
  if (conversationId) idLines.push(`conversation_id: ${conversationId}`);
  // user_message_id first: regenerate / log lookup need the persisted user row, not a
  // client-only assistant UUID created before a failed stream.
  if (userMessageId) idLines.push(`user_message_id: ${userMessageId}`);
  if (messageId && messageId !== userMessageId) {
    idLines.push(`message_id: ${messageId}`);
  } else if (messageId && !userMessageId) {
    idLines.push(`message_id: ${messageId}`);
  }
  if (traceId) idLines.push(`trace_id: ${traceId}`);
  if (executionId) idLines.push(`execution_id: ${executionId}`);
  if (idLines.length === 0) return "";

  const lines = ["阅读这段产品AI日志：", ...idLines, ...extraLinesFromIds(ids)];
  if (traceId) {
    lines.push(`uv run python scripts/log_timeline.py --trace ${traceId}`);
  } else if (conversationId) {
    lines.push(`uv run python scripts/log_timeline.py ${conversationId}`);
  }

  return lines.join("\n");
}
