import {
  type RecoveryMomentFields,
  withRecoveryMoment,
} from "@/lib/recoveryMoment";
import { ApiError, NetworkError } from "@/services/api";
import {
  KEY_CONFIG_ERROR_CODES,
  NON_RETRIABLE_ERROR_CODES,
  isUnstartedSendRefusal as matchUnstartedSendRefusal,
} from "@agentcore/contract-types";
import {
  EMPTY_RESPONSE_CHIP_LABELS,
  LLM_EMPTY_RESPONSE_MESSAGE,
  LLM_ERROR_MESSAGE,
  LLM_UNPRODUCTIVE_MESSAGE,
  TURN_INTERRUPTED_EMPTY_MESSAGE,
  degradedFinishChipLabel,
  isEmptyResponseUserSurface,
} from "@agentcore/protocol-fold-kit";

export {
  EMPTY_RESPONSE_CHIP_LABELS,
  LLM_EMPTY_RESPONSE_MESSAGE,
  LLM_ERROR_MESSAGE,
  LLM_UNPRODUCTIVE_MESSAGE,
  TURN_INTERRUPTED_EMPTY_MESSAGE,
  degradedFinishChipLabel,
  isEmptyResponseUserSurface,
};

/**
 * One place that turns any backend / transport error into the three things the
 * UI needs: a zh message, an optional one-click remedy, and whether an immediate
 * retry is worth offering. Both the REST client ({@link ApiError}) and the SSE
 * turn ({@link StreamError}) feed through here, so a given error `code` is phrased
 * and actioned identically wherever it surfaces — the turn banner, the inline
 * mid-stream card, and the REST toast.
 *
 * The backend is the single source of the error contract: every failure is
 * `{ error: { code, message } }` plus an HTTP status (and `Retry-After` on
 * cool-downs), produced by the global handler in `apps/server` over the
 * `AgentCoreError` hierarchy (`core/errors.py`). That `message` is already a
 * user-facing zh string for most coded errors, so we prefer it verbatim and only
 * fall back to generic phrasing when it is absent.
 */

// "sidecar" = a local-engine turn failure (spawn/init/engine/exit) whose precise
// reason rides on `serverMessage` — it is neither a real network outage nor an
// auth issue, so it falls through to the serverMessage branch in resolveMessage
// (a generic "network" banner would mask why the local engine couldn't run).
export type StreamErrorKind = "network" | "http" | "auth" | "sidecar";

/**
 * A transport-level failure of an SSE turn (distinct from a backend `error`
 * event, which is delivered inline). Carries a kind so the UI can phrase it, plus
 * the backend's `code` / `message` / `Retry-After` when the turn was refused with
 * a plain JSON 4xx (quota / rate limit / missing key) rather than an event stream.
 */
export class StreamError extends Error {
  code?: string;
  serverMessage?: string;
  retryAfter?: number;
  /** 本回合在产生任何可见输出 / 副作用之前就失败了——调用方可安全地改走另一条链路重跑整轮
   * 而不重复输出 / 副作用。当前用途：sidecar 启动期失败（引擎没跑起来）自动降级回云端。 */
  recoverable?: boolean;
  /** 上游额度恢复 / 平台配额重置的绝对时刻（ISO8601 UTC，原样保留）——由渲染层按用户
   * 本机时区成文（{@link withRecoveryMoment}），服务端句子里已不含时刻。 */
  recoveryMoment?: RecoveryMomentFields;

  constructor(
    public kind: StreamErrorKind,
    public status?: number,
    extra?: {
      code?: string;
      serverMessage?: string;
      retryAfter?: number;
      recoverable?: boolean;
      recoveryMoment?: RecoveryMomentFields;
    },
  ) {
    super(`stream ${kind}${status ? ` ${status}` : ""}`);
    this.name = "StreamError";
    this.code = extra?.code;
    this.serverMessage = extra?.serverMessage;
    this.retryAfter = extra?.retryAfter;
    this.recoverable = extra?.recoverable;
    this.recoveryMoment = extra?.recoveryMoment;
  }
}

/**
 * Build a {@link StreamError} from a non-OK response. A refused turn (e.g. 429
 * for quota / rate limit, 403 for CSRF) arrives as a plain JSON
 * `{error:{code,message}}` body with an optional `Retry-After` header — not an
 * event stream — so pull those out for precise UI phrasing. Falls back to
 * status-only when the body isn't the expected shape.
 *
 * Every SSE-over-POST channel (chat turn, mid-flight send, workspace handoff)
 * must go through this: a channel that throws on the status alone phrases the
 * *same* backend refusal as a bare「操作失败（403）」while its sibling shows the
 * real reason.
 */
export async function streamErrorFromResponse(
  response: Response,
): Promise<StreamError> {
  let code: string | undefined;
  let serverMessage: string | undefined;
  let recoveryMoment: RecoveryMomentFields | undefined;
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string } & RecoveryMomentFields;
      detail?: { code?: string; message?: string } | string;
    };
    code = body.error?.code;
    serverMessage = body.error?.message;
    if (body.error) {
      recoveryMoment = {
        recovery_at: body.error.recovery_at,
        reset_at: body.error.reset_at,
      };
    }
    if (!code && typeof body.detail === "object" && body.detail) {
      code = body.detail.code;
      serverMessage = body.detail.message ?? serverMessage;
    }
  } catch {
    /* non-JSON body — keep status-only phrasing */
  }
  const header = Number(response.headers.get("Retry-After"));
  return new StreamError("http", response.status, {
    code,
    serverMessage,
    retryAfter: Number.isFinite(header) && header > 0 ? header : undefined,
    recoveryMoment,
  });
}

/** Product copy for upstream 429 (mirrors backend LLMRateLimitError / history 注记). */
export const LLM_RATE_LIMIT_WHY = "上游限流，暂时无法继续本回合。";
export const LLM_RATE_LIMIT_MESSAGE = `${LLM_RATE_LIMIT_WHY}请稍后再试。`;

/** Product copy when the desktop client is below the server force-update floor. */
export const CLIENT_TOO_OLD_MESSAGE = "桌面端版本过旧，请更新后再试";

/**
 * Product copy for a rejected security token (backend `CSRF_FAILED`, HTTP 403).
 *
 * The backend ships an English developer sentence for this one ("CSRF token
 * missing or invalid. Re-login and retry."), so the usual verbatim-passthrough
 * would put untranslated ops copy on a red banner — and it overstates the fix.
 * The 403 itself hands back a usable token (middleware/csrf.py), which the api
 * layer has already absorbed by the time this renders, so replaying the same
 * request is all it takes. Say exactly that: no re-login, no page reload.
 */
export const CSRF_FAILED_MESSAGE = "安全校验未通过，请重试。";

/**
 * Codes whose backend `message` is not user-ready, mapped to the zh sentence that
 * replaces it. Kept separate from {@link PRODUCT_COPY_BY_CODE} (a fallback for
 * when the server said nothing): these override even a present server message,
 * so every surface — toast, banner, inline form message — reads the same.
 */
const COPY_OVERRIDE_BY_CODE: Record<string, string> = {
  CSRF_FAILED: CSRF_FAILED_MESSAGE,
};

/** zh copy that must replace the backend message, or null to keep it verbatim. */
export function productCopyOverride(code: string | undefined): string | null {
  if (!code) return null;
  return COPY_OVERRIDE_BY_CODE[code] ?? null;
}

/** Assistant bubble error text; in dev, append upstream body preview when present. */
export function formatAssistantErrorMessage(error: {
  message: string;
  code?: string;
  context?: DescribedError["context"];
}): string {
  const { message, context, code } = error;
  // Old journals may still carry English "Rate limited…" — normalize to product copy.
  let text =
    code === "LLM_RATE_LIMIT" &&
    (!message || /rate limited/i.test(message) || !message.includes("上游限流"))
      ? LLM_RATE_LIMIT_MESSAGE
      : message;
  // 后端句子里已不含时刻，只在 context 上给绝对瞬间——这里按本机时区补一句。
  text = withRecoveryMoment(text, context);
  if (import.meta.env.DEV && context?.upstream_body_preview) {
    text = `${text} — ${context.upstream_body_preview}`;
  }
  return text;
}

/**
 * Connectivity / transport-ish codes — bubble offers「重试」, not settings, and a
 * repeat inside one chat may escalate into the Base URL / API Key hint.
 *
 * A 429 (``LLM_RATE_LIMIT``) is deliberately absent: the upstream answered, it
 * just refused for now (per-minute cool-down, or the day's allowance spent). The
 * Base URL and Key are provably fine — the same credentials reached the vendor to
 * earn that 429 — so escalating sends the user to "fix" a correct config. The card
 * already says when the allowance comes back.
 */
const CONNECTIVITY_ERROR_CODES: readonly string[] = [
  "LLM_TIMEOUT",
  "LLM_ERROR",
  "LLM_UPSTREAM_ERROR",
];

/**
 * Our-cloud faults (pool / billing / key storage / internal) — never treat as
 * vendor Base URL / API Key connectivity. Codes must stay out of
 * {@link CONNECTIVITY_ERROR_CODES} so session escalation counters stay clean.
 */
const OUR_SERVICE_ERROR_CODES: readonly string[] = [
  "DATABASE_UNAVAILABLE",
  "KEY_STORAGE_UNAVAILABLE",
  "PLATFORM_BILLING_UNAVAILABLE",
  "INTERNAL_ERROR",
];

/** Product copy when our cloud (not the vendor) is busy / unavailable. */
export const OUR_SERVICE_UNAVAILABLE_MESSAGE =
  "AgentCore 服务暂时不可用，请稍后重试";

/**
 * Connectivity failure counts for the conversation currently on screen.
 *
 * "Session" is the chat, not the renderer process. An Electron window lives for
 * days without a reload, so counting for the process lifetime let a brand-new
 * chat escalate on its *first* failure because of an unrelated timeout hours
 * earlier — the copy says「多次连接失败」, so it has to mean multiple failures the
 * user actually just saw. Moving to another conversation drops the counts, which
 * also bounds the counted-id set.
 */
let _connectivityScope: string | null = null;
const _sessionConnectivityCounts = new Map<string, number>();
/** Message ids already counted — format/render must not double-increment. */
const _countedErrorMessageIds = new Set<string>();

/** Start over when the failing bubbles belong to a different conversation. */
function enterConnectivityScope(
  conversationId: string | null | undefined,
): void {
  const scope = conversationId ?? null;
  if (scope === _connectivityScope) return;
  _connectivityScope = scope;
  _sessionConnectivityCounts.clear();
  _countedErrorMessageIds.clear();
}

export function isConnectivityErrorCode(code: string | undefined): boolean {
  return (
    code !== undefined &&
    (CONNECTIVITY_ERROR_CODES as readonly string[]).includes(code) &&
    !(OUR_SERVICE_ERROR_CODES as readonly string[]).includes(code)
  );
}

/** Preflight 402/429/平台凭据缺失：发送当没发生。须再配「用户消息未落库」。 */
export function isUnstartedSendRefusal(err: unknown): boolean {
  const f = factsOf(err);
  return matchUnstartedSendRefusal({ code: f.code, status: f.status });
}

/** True when the failure is our cloud (not vendor Base URL / API Key). */
export function isOurServiceErrorCode(code: string | undefined): boolean {
  return (
    code !== undefined &&
    (OUR_SERVICE_ERROR_CODES as readonly string[]).includes(code)
  );
}

/** Increment once per message id; return the count for that code in this chat. */
export function noteSessionConnectivityFailure(
  code: string,
  messageId: string,
  conversationId?: string | null,
): number {
  enterConnectivityScope(conversationId);
  if (!_countedErrorMessageIds.has(messageId)) {
    _countedErrorMessageIds.add(messageId);
    _sessionConnectivityCounts.set(
      code,
      (_sessionConnectivityCounts.get(code) ?? 0) + 1,
    );
  }
  return _sessionConnectivityCounts.get(code) ?? 0;
}

/** True when the failure is a request/params rejection, not transport/connectivity. */
export function isClientSideLlmRejection(opts?: {
  message?: string | null;
  upstreamStatus?: number;
}): boolean {
  const status = opts?.upstreamStatus;
  // 4xx (except 429 rate limit) are client/request problems — do not escalate
  // them into "check Base URL / API Key / network".
  if (status !== undefined && status >= 400 && status < 500 && status !== 429) {
    return true;
  }
  const msg = (opts?.message ?? "").toLowerCase();
  if (!msg) return false;
  return (
    msg.includes("invalid_request") ||
    msg.includes("请求参数") ||
    msg.includes("不被当前模型支持") ||
    msg.includes("请求格式被拒绝") ||
    msg.includes("cc switch")
  );
}

/**
 * Escalation copy for the 2nd+ connectivity failure in this chat.
 * Side-effect: counts this message id at most once, under `opts.conversationId`.
 * Skips client-side request rejections (e.g. upstream 400 invalid_request).
 */
export function connectivityEscalationSuffix(
  code: string | undefined,
  messageId: string,
  opts?: {
    message?: string | null;
    upstreamStatus?: number;
    /** Empty-response diagnosis — never escalate into Base URL / API Key copy. */
    emptyDiagnosis?: string;
    /** Scopes the counter; a different chat starts counting from zero. */
    conversationId?: string | null;
  },
): string | null {
  // LLM_EMPTY_RESPONSE is not a connectivity code; still guard explicitly so a
  // future catalog slip cannot append「检查 Base URL / API Key」onto the red card.
  if (code === "LLM_EMPTY_RESPONSE") return null;
  if (opts?.emptyDiagnosis) return null;
  // Our-cloud 5xx (pool / internal): honest retry, never「设置 · 服务商」.
  if (isOurServiceErrorCode(code)) return null;
  if (!code || !isConnectivityErrorCode(code)) return null;
  if (isClientSideLlmRejection(opts)) return null;
  const n = noteSessionConnectivityFailure(
    code,
    messageId,
    opts?.conversationId,
  );
  if (n < 2) return null;
  return "\n\n多次连接失败。请到「设置 · 服务商」检查 Base URL / API Key 与网络后重试。";
}

/** Test helper — clear the connectivity counters and their conversation scope. */
export function resetSessionConnectivityFailures(): void {
  _connectivityScope = null;
  _sessionConnectivityCounts.clear();
  _countedErrorMessageIds.clear();
}

/** Empty cancelled (user Stop) — synthetic code for fold/preview skips; chat timeline omits the face (P1). */
export const TURN_CANCELLED_EMPTY_MESSAGE = "已停止";

/**
 * Platform auth dead product sentence (align byok/platform 甲; not byok main fix).
 * Used when empty cancelled/error carries ``LLM_KEY_INVALID`` without a live message.
 */
export const PLATFORM_AUTH_UNAVAILABLE_MESSAGE =
  "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。";

/** Generic empty-failure fallback when no code/message product sentence exists. */
export const GENERIC_EMPTY_FAILURE_MESSAGE = "本轮未能完成，请重试。";

/** Code → product sentence (tier-1 face copy). Unknown codes fall through to generic. */
const PRODUCT_COPY_BY_CODE: Record<string, string> = {
  LLM_RATE_LIMIT: LLM_RATE_LIMIT_MESSAGE,
  LLM_KEY_INVALID: PLATFORM_AUTH_UNAVAILABLE_MESSAGE,
  LLM_UNPRODUCTIVE: LLM_UNPRODUCTIVE_MESSAGE,
  LLM_INSUFFICIENT_BALANCE: "上游账户余额不足，请充值或更换 Key。",
  LLM_TIMEOUT: "连接超时，请检查网络后重试。",
  LLM_EMPTY_RESPONSE: LLM_EMPTY_RESPONSE_MESSAGE,
  PIPELINE_ERROR: "管线执行失败，请重试。",
  TURN_INTERRUPTED: TURN_INTERRUPTED_EMPTY_MESSAGE,
  TURN_CANCELLED: TURN_CANCELLED_EMPTY_MESSAGE,
  LLM_ERROR: LLM_ERROR_MESSAGE,
  DATABASE_UNAVAILABLE: OUR_SERVICE_UNAVAILABLE_MESSAGE,
  KEY_STORAGE_UNAVAILABLE: OUR_SERVICE_UNAVAILABLE_MESSAGE,
  PLATFORM_BILLING_UNAVAILABLE: OUR_SERVICE_UNAVAILABLE_MESSAGE,
  INTERNAL_ERROR: OUR_SERVICE_UNAVAILABLE_MESSAGE,
};

/** Product sentence for a known failure code, or undefined to keep the server message. */
export function productCopyForCode(
  code: string | undefined,
): string | undefined {
  if (!code) return undefined;
  return PRODUCT_COPY_BY_CODE[code];
}

export type AssistantFailureFace = { code: string; message: string };

type StructuredErr =
  | {
      code?: string | null;
      message?: string | null;
    }
  | null
  | undefined;

/**
 * Single authority for assistant failure face (空泡族根因重设计).
 *
 * Default ON: empty content + any structured error source, or empty + failure
 * finishReason. Short silent exemption list:
 * - user-initiated stop (cancelled / TURN_CANCELLED) — chat timeline omits face
 * - paused (always silent, card or not) — structured error still surfaces
 *
 * Copy tiers: structured message → code product sentence → generic fallback.
 */
export function resolveAssistantFailureFace(input: {
  content?: string | null;
  isStreaming?: boolean;
  error?: StructuredErr;
  runsError?: StructuredErr;
  usageError?: StructuredErr;
  finishReason?: string | null;
  /** True when pause/ask/checkpoint/plan_review/… card already carries the turn. */
  hasDedicatedPauseOrAskUi?: boolean;
}): AssistantFailureFace | null {
  if (input.isStreaming) return null;

  const structured =
    coalesceStructured(input.error) ??
    coalesceStructured(input.runsError) ??
    coalesceStructured(input.usageError);

  const fr = input.finishReason ?? undefined;
  const empty = !(input.content ?? "").trim();

  // Auth / key codes win even when local settle stamped cancelled.
  if (structured?.code === "LLM_KEY_INVALID") {
    return faceFromStructured(structured);
  }
  if (structured?.code === "LLM_RATE_LIMIT") {
    return faceFromStructured(structured);
  }

  // User-stop exemption: still return TURN_CANCELLED so StatusStrip / preview
  // can label; chat timeline hides via isUserStopped / isEmptyCancelledAssistant.
  // Auth / rate-limit codes already returned above (win over cancelled).
  if (structured?.code === "TURN_CANCELLED" || fr === "cancelled") {
    return {
      code: "TURN_CANCELLED",
      message: TURN_CANCELLED_EMPTY_MESSAGE,
    };
  }

  if (structured) {
    return faceFromStructured(structured);
  }

  // No structured payload — synthesize from finishReason.
  if (!empty && fr === "error") {
    return { code: "LLM_ERROR", message: PRODUCT_COPY_BY_CODE.LLM_ERROR };
  }
  if (!empty) return null;

  if (fr === "unproductive") {
    return {
      code: "LLM_UNPRODUCTIVE",
      message: LLM_UNPRODUCTIVE_MESSAGE,
    };
  }
  if (fr === "interrupted") {
    return {
      code: "TURN_INTERRUPTED",
      message: TURN_INTERRUPTED_EMPTY_MESSAGE,
    };
  }
  if (fr === "error") {
    return { code: "LLM_ERROR", message: PRODUCT_COPY_BY_CODE.LLM_ERROR };
  }
  if (fr === "degraded") {
    return {
      code: "LLM_EMPTY_RESPONSE",
      message: PRODUCT_COPY_BY_CODE.LLM_EMPTY_RESPONSE,
    };
  }
  if (fr === "paused") return null;
  return null;
}

function coalesceStructured(
  err: StructuredErr,
): { code: string; message: string } | null {
  if (!err) return null;
  const code = (err.code ?? "").trim();
  const message = (err.message ?? "").trim();
  if (!code && !message) return null;
  return { code: code || "LLM_ERROR", message };
}

function faceFromStructured(err: {
  code: string;
  message: string;
}): AssistantFailureFace {
  const code = err.code || "LLM_ERROR";
  if (err.message.trim()) {
    // Prefer upstream/product message; normalize known rate-limit English.
    if (
      code === "LLM_RATE_LIMIT" &&
      (/rate limited/i.test(err.message) || !err.message.includes("上游限流"))
    ) {
      return { code, message: LLM_RATE_LIMIT_MESSAGE };
    }
    return { code, message: err.message.trim() };
  }
  return {
    code,
    message: PRODUCT_COPY_BY_CODE[code] ?? GENERIC_EMPTY_FAILURE_MESSAGE,
  };
}

/**
 * When reload lost the error payload but left an empty failure-finished bubble,
 * synthesize a minimal card. Thin wrapper over {@link resolveAssistantFailureFace}
 * (finishReason + optional code only).
 */
export function syntheticErrorForEmptyFailure(
  finishReason: string | undefined,
  code?: string | null,
): {
  code: string;
  message: string;
} | null {
  return resolveAssistantFailureFace({
    content: "",
    finishReason,
    runsError: code ? { code, message: "" } : null,
  });
}

/**
 * Hard-fail red card when `finishReason=error` but `message.error` is missing.
 * Prefer `runs.error` copy when present; else the empty-failure synthetic.
 */
export function syntheticErrorForHardFailure(
  finishReason: string | undefined,
  runsError?: { code?: string | null; message?: string | null } | null,
): {
  code: string;
  message: string;
} | null {
  if (finishReason !== "error") return null;
  return resolveAssistantFailureFace({
    content: "",
    finishReason,
    runsError,
  });
}

/**
 * Visible sentence for preview / export / canvas outlets that otherwise only
 * read `content`. Non-empty trimmed content wins (partial deliverable); pure
 * failure falls back to `error.message` then `runs.error.message` then
 * `usage.error.message`.
 */
export function visibleMessageText(msg: {
  content?: string | null;
  error?: { message?: string } | null;
  runs?: { error?: { message?: string } | null } | null;
  usage?: { error?: { message?: string } | null } | null;
}): string {
  const content = (msg.content ?? "").trim();
  if (content) return content;
  const fromError = msg.error?.message?.trim();
  if (fromError) return fromError;
  const fromRuns = msg.runs?.error?.message?.trim();
  if (fromRuns) return fromRuns;
  const fromUsage = msg.usage?.error?.message?.trim();
  if (fromUsage) return fromUsage;
  return "";
}

/**
 * A one-click remedy that fixes the *cause* of an error by routing the user
 * somewhere (e.g. the model-config page to add a BYOK key), rather than retrying
 * the same operation. `href` is a hash-router path.
 */
export interface ErrorAction {
  label: string;
  href: string;
}

/**
 * Normalized, user-facing view of an error. A `null` return from
 * {@link describeError} means the UI should stay silent — auth failures already
 * redirect to the login screen, so a banner/toast on top would be noise.
 */
export interface DescribedError {
  message: string;
  action: ErrorAction | null;
  retriable: boolean;
  code?: string;
  /** 只放用户自己能行动的事实。运营方中转账号的诊断（Sub2API）不进这里，也不进气泡
   * ——平台模式下用户没有自己的 key，那说的是别人的账号。后端只写日志。 */
  context?: {
    upstream_status?: number;
    upstream_body_preview?: string | null;
    retry_attempts?: number;
    empty_diagnosis?: string;
    body_kind?: string;
    base_url?: string;
    retry_after?: number;
    credential_source?: "user" | "platform" | string | null;
    /** 上游额度恢复的绝对时刻（ISO8601 UTC）；按本机时区成文，缺省即不提时刻。 */
    recovery_at?: string | null;
    /** 平台配额闸门的重置时刻（ISO8601 UTC），同上。 */
    reset_at?: string | null;
  };
}

/**
 * Map a backend error `code` to a config remedy. Auth / key / balance → settings;
 * connectivity codes return null (the bubble shows「重试」instead).
 *
 * ``LLM_KEY_INVALID`` CTA 按凭据来源分流（甲）：
 * - user BYOK →「去服务商」换 Key
 * - platform →「接入自己的 Key」（与 QUOTA_EXCEEDED 同出口；主文案已引导联系管理员）
 * ``INFERENCE_TOKEN_EXPIRED`` 永不进 settings。
 */
export function errorActionForCode(
  code: string | undefined,
  opts?: {
    credentialSource?: string | null;
    message?: string | null;
  },
): ErrorAction | null {
  // Inference JWT ≠ BYOK key — never push「去服务商」.
  if (code === "INFERENCE_TOKEN_EXPIRED") {
    return null;
  }
  // Our cloud busy / misconfigured — retry (or wait), not vendor settings.
  if (isOurServiceErrorCode(code)) {
    return null;
  }
  if (code === "LLM_KEY_INVALID") {
    const src =
      opts?.credentialSource === "platform" || opts?.credentialSource === "user"
        ? opts.credentialSource
        : opts?.message?.includes("平台模型暂时不可用")
          ? "platform"
          : "user";
    if (src === "platform") {
      return { label: "接入自己的 Key", href: "/more/providers" };
    }
    return { label: "去服务商", href: "/more/providers" };
  }
  if (
    code !== undefined &&
    (KEY_CONFIG_ERROR_CODES as readonly string[]).includes(code)
  ) {
    return { label: "去服务商", href: "/more/providers" };
  }
  // 平台额度耗尽 (QUOTA_EXCEEDED, 成本配额与计费 §〇·六 F6): 主文案是等重置 / 联系管理员，
  // 这里补一个「接入自己的 Key」次级出口 —— byok 回合不查配额, 是真正的绕过路径。
  if (code === "QUOTA_EXCEEDED") {
    return { label: "接入自己的 Key", href: "/more/providers" };
  }
  // Always-entry write gate (记忆 · 配额闸在写侧): create / promote past the cap —
  // send the user to the file rail to shrink or demote always entries.
  if (code === "ALWAYS_QUOTA_EXCEEDED") {
    return { label: "去整理", href: "/files" };
  }
  return null;
}

/** The facts the message/action/retry rules read, extracted once from any error
 * shape so the rules below never branch on the concrete class. */
interface ErrorFacts {
  status?: number;
  code?: string;
  serverMessage?: string;
  retryAfter?: number;
  context?: DescribedError["context"];
  /** 结构化恢复 / 重置时刻，成文交给 {@link withRecoveryMoment}。 */
  recoveryMoment?: RecoveryMomentFields;
  transport: boolean;
  auth: boolean;
}

function factsOf(err: unknown): ErrorFacts {
  if (err instanceof StreamError) {
    return {
      status: err.status,
      code: err.code,
      serverMessage: err.serverMessage,
      retryAfter: err.retryAfter,
      recoveryMoment: err.recoveryMoment,
      transport: err.kind === "network",
      auth: err.kind === "auth",
    };
  }
  if (err instanceof ApiError) {
    return {
      status: err.status,
      code: err.code,
      serverMessage: err.serverMessage,
      retryAfter: err.retryAfter,
      recoveryMoment: err.recoveryMoment,
      transport: false,
      auth: err.status === 401,
    };
  }
  if (err instanceof NetworkError) {
    return { transport: true, auth: false };
  }
  return { transport: false, auth: false };
}

function resolveMessage(f: ErrorFacts): string {
  if (f.transport) return "网络连接中断，请检查网络后重试";
  // Force-update floor: CLIENT_TOO_OLD or HTTP 426 Upgrade Required.
  if (f.code === "CLIENT_TOO_OLD" || f.status === 426) {
    return CLIENT_TOO_OLD_MESSAGE;
  }
  const override = productCopyOverride(f.code);
  if (override) return override;
  // A 429 is a deliberate refusal (quota used up, or sending too fast), not an
  // outage. The backend ships the zh sentence minus the moment (which rides
  // structured, and describeError appends in the user's own timezone); prefer it,
  // otherwise phrase the wait from Retry-After.
  if (f.status === 429) {
    if (f.serverMessage) return f.serverMessage;
    if (f.retryAfter) return `操作过于频繁，请约 ${f.retryAfter} 秒后再试`;
    return "操作过于频繁或额度已用尽，请稍后再试";
  }
  if (f.code === "pending_interactions_awaiting") {
    return f.serverMessage ?? "有待拍板的确认卡，先处理或停止当前任务";
  }
  if (f.code === "turn_in_progress") {
    // Residual 409 only (non-cold-resume / older server). Cold resume busy is
    // EPHEMERAL `resume_deferred` on the same SSE — not this error path; do not
    // toast this line as a deferred-success state.
    return "回合收尾尚未完成，请稍候或先显式停止后再试";
  }
  // A 402 LLM_KEY_REQUIRED is a deliberate BYOK refusal (no DeepSeek key yet);
  // surface the backend's actionable message (or a config hint), never a
  // misleading "service unavailable".
  if (f.code === "LLM_RATE_LIMIT") {
    if (f.serverMessage?.includes("上游限流")) {
      return f.serverMessage;
    }
    return LLM_RATE_LIMIT_MESSAGE;
  }
  if (f.code === "LLM_KEY_REQUIRED") {
    return f.serverMessage ?? "请先接入自己的 API Key，再发起对话。";
  }
  if (f.code === "INFERENCE_TOKEN_EXPIRED") {
    return (
      f.serverMessage ??
      "本地与云端的推理凭证已失效或过期。请稍后再试（将自动换新凭证）；仍失败请重新登录后再试。"
    );
  }
  if (isOurServiceErrorCode(f.code)) {
    return f.serverMessage ?? OUR_SERVICE_UNAVAILABLE_MESSAGE;
  }
  // Legacy engine builds still surface the English JWT rejection under LLM_KEY_INVALID.
  if (
    f.serverMessage &&
    /invalid or expired inference token/i.test(f.serverMessage)
  ) {
    return "本地与云端的推理凭证已失效或过期。请稍后再试（将自动换新凭证）；仍失败请重新登录后再试。";
  }
  if (f.code === "ADMIN_PRODUCT_FORBIDDEN") {
    return "此账号为管理员账号，请使用管理后台登录";
  }
  // Most coded errors carry a user-facing zh message (validation / conflict /
  // invalid key / insufficient balance …) — prefer it verbatim (single-sourced).
  if (f.serverMessage) {
    if (import.meta.env.DEV && f.context?.upstream_body_preview) {
      return `${f.serverMessage} — ${f.context.upstream_body_preview}`;
    }
    return f.serverMessage;
  }
  if (import.meta.env.DEV && f.context?.upstream_status) {
    const preview = f.context.upstream_body_preview
      ? ` — ${f.context.upstream_body_preview}`
      : "";
    return `上游推理错误（HTTP ${f.context.upstream_status}${preview}）`;
  }
  if (f.status && f.status >= 500)
    return `服务暂时不可用（${f.status}），请重试`;
  if (f.status) return `操作失败（${f.status}），请重试`;
  return "操作失败，请重试";
}

/**
 * Whether an error means "this backend build doesn't offer this endpoint" — a 404
 * (route not registered) or 501 (declared but not implemented). Distinct from a
 * transient failure: retrying won't help until the server is upgraded, so a caller
 * degrades to a calm "feature unavailable" state (no red 加载失败, no retry) instead
 * of an alarming error. Guards the 前后端版本漂移 window — a newer client calling an
 * endpoint the older *deployed* backend lacks (e.g. 记忆·主题 shipped in the client
 * before the backend redeploy). NOT for 401 (auth, handled by redirect) or 5xx
 * outages (transient, worth a retry).
 */
export function isFeatureUnavailable(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 501);
}

/**
 * Normalize any error into the {@link DescribedError} the UI shows, or `null`
 * when the UI should stay silent (auth → the login redirect handles it).
 */
export function describeError(err: unknown): DescribedError | null {
  const f = factsOf(err);
  if (f.auth) return null;
  const inferenceTokenFailure =
    f.code === "INFERENCE_TOKEN_EXPIRED" ||
    (f.serverMessage != null &&
      /invalid or expired inference token/i.test(f.serverMessage));
  return {
    // 服务端只说「额度恢复前重试仍会失败」，恢复时刻按用户本机时区在这里补上。
    message: withRecoveryMoment(resolveMessage(f), f.recoveryMoment),
    action: inferenceTokenFailure
      ? null
      : errorActionForCode(f.code, {
          credentialSource: f.context?.credential_source,
          message: f.serverMessage,
        }),
    // Suppress retry on refusals that an immediate re-send can't fix (quota used /
    // key missing-or-invalid / wallet empty / server key-storage down / free tier
    // exhausted). Inference JWT expiry is remintable — keep retry, and so is a CSRF
    // rejection: the 403 re-armed the client, so the re-send is the fix. The shared
    // contract-types catalog is the single source for the rest.
    retriable: inferenceTokenFailure
      ? true
      : f.code === "CLIENT_TOO_OLD" || f.status === 426
        ? false
        : !(
            f.code !== undefined &&
            (NON_RETRIABLE_ERROR_CODES as readonly string[]).includes(f.code)
          ),
    code: f.code,
    context: f.context,
  };
}

// ---- Streaming-turn helpers (thin wrappers over describeError) --------------
// Named for the SSE turn flow (banner + retry) and consumed by the turn resolver
// in services/turns.ts. They share describeError's code map so a turn banner and
// a REST toast phrase the same backend code identically.

/** zh message for a failed turn, or null when no banner should show. */
export function describeStreamError(err: unknown): string | null {
  return describeError(err)?.message ?? null;
}

/** Whether a failed turn should offer a retry. */
export function isRetriableStreamError(err: unknown): boolean {
  return describeError(err)?.retriable ?? true;
}

/** The config remedy for a failed turn, if any. */
export function streamErrorAction(err: unknown): ErrorAction | null {
  return describeError(err)?.action ?? null;
}
