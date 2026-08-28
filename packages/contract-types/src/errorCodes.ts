// AgentCore error-code contract — the single shared directory of user-facing error
// codes for BOTH desktop and mobile. The code list is generated from the backend
// ErrorCode StrEnum (`pnpm gen:types` → errorCodes.generated.ts). Policy overlays
// below (key-config remedy / non-retriable) stay hand-written — they are client UX
// data, not the catalog itself.

import { ERROR_CODES, type ErrorCode } from "./errorCodes.generated";

export { ERROR_CODES, type ErrorCode };

/** Codes whose remedy is to (re)configure the BYOK key — each client offers its
 * own one-click config route instead of a retry. `LLM_INSUFFICIENT_BALANCE` belongs
 * here too: a drained wallet has two real exits — top up at the vendor (carried by the
 * backend's own message) or swap in a different key — and the config page is exactly
 * where the second one happens. Suppressing retry is a separate concern
 * ({@link NON_RETRIABLE_ERROR_CODES}); this list only decides the one-click route. */
export const KEY_CONFIG_ERROR_CODES: readonly ErrorCode[] = [
  "LLM_KEY_REQUIRED",
  "LLM_KEY_INVALID",
  "LLM_INSUFFICIENT_BALANCE",
];

/** Codes where an immediate retry is pointless until the user acts — top up the wallet,
 * fix/add the key, wait for quota, or fix server config. The client suppresses the
 * retry affordance for these (rate limits stay retriable: they clear on their own). */
export const NON_RETRIABLE_ERROR_CODES: readonly ErrorCode[] = [
  "QUOTA_EXCEEDED",
  "LLM_KEY_REQUIRED",
  "LLM_KEY_INVALID",
  "LLM_INSUFFICIENT_BALANCE",
  "KEY_STORAGE_UNAVAILABLE",
  "PLATFORM_BILLING_UNAVAILABLE",
  "CONTEXT_OVERFLOW",
];

/** Type guard: whether `code` is a code the clients recognize (typed against the
 * shared catalog), letting call sites narrow an opaque wire string to `ErrorCode`. */
export function isKnownErrorCode(code: string | undefined): code is ErrorCode {
  return (
    code !== undefined && (ERROR_CODES as readonly string[]).includes(code)
  );
}

/**
 * Preflight refusals: the turn never started (SSE 未开、用户消息未落库).
 * Clients roll back the optimistic send and restore the composer draft.
 * Mid-turn failures that reuse a code here after persist are **not** this class —
 * callers must also require "turn never persisted".
 */
export const UNSTARTED_SEND_REFUSAL_CODES: readonly ErrorCode[] = [
  "LLM_KEY_REQUIRED",
  "QUOTA_EXCEEDED",
  "RATE_LIMITED",
  "PLATFORM_BILLING_UNAVAILABLE",
  "CONTEXT_OVERFLOW",
];

export function isUnstartedSendRefusalCode(
  code: string | undefined,
): boolean {
  return (
    code !== undefined &&
    (UNSTARTED_SEND_REFUSAL_CODES as readonly string[]).includes(code)
  );
}

/** Coded allowlist, or a bare 402 / 429 (product preflight only uses those
 * statuses for key / quota / rate-limit). Bare 503 is **not** included — that
 * can be a generic outage. Pair with a not-persisted check at the call site. */
export function isUnstartedSendRefusal(opts: {
  code?: string;
  status?: number;
}): boolean {
  if (isUnstartedSendRefusalCode(opts.code)) return true;
  return opts.status === 402 || opts.status === 429;
}

/**
 * First-upstream capability / rate failures that may be treated as
 * 「发送当没发生」**only when** the assistant is empty, tokens are 0, and no
 * tools ran. Do **not** fold these into {@link UNSTARTED_SEND_REFUSAL_CODES} —
 * the same codes mid-turn (after content or tools) must stay a failed turn.
 */
export const ZERO_OUTPUT_SEND_REFUSAL_CODES: readonly ErrorCode[] = [
  "LLM_RATE_LIMIT",
  "LLM_KEY_INVALID",
  "LLM_INSUFFICIENT_BALANCE",
];

export function isZeroOutputSendRefusalCode(
  code: string | undefined,
): boolean {
  return (
    code !== undefined &&
    (ZERO_OUTPUT_SEND_REFUSAL_CODES as readonly string[]).includes(code)
  );
}
