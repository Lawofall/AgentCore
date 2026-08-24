/** Longest upstream ``Retry-After`` an interactive turn will sit out — mirrored in
 * ``apps/server/agentcore/core/errors.py`` ``MAX_RETRY_AFTER``. Past this ceiling
 * the copy says retrying keeps failing until the allowance returns; clients suppress
 * retry when ``retry_after`` is attested above it. Comparison uses ``>`` so exactly
 * this many seconds may still retry. */
export const MAX_RETRY_AFTER = 30;

/** Error codes that represent upstream / route rate limiting (not quota walls). */
export const RATE_LIMIT_FAMILY_CODES = [
  "RATE_LIMITED",
  "LLM_RATE_LIMIT",
] as const;

export type RateLimitFamilyCode = (typeof RATE_LIMIT_FAMILY_CODES)[number];

export function isRateLimitFamilyCode(
  code: string | null | undefined,
): code is RateLimitFamilyCode {
  return (
    code !== null &&
    code !== undefined &&
    (RATE_LIMIT_FAMILY_CODES as readonly string[]).includes(code)
  );
}

/** True when an attested ``retry_after`` is past the interactive ceiling. */
export function rateLimitRetrySuppressed(
  retryAfterSec: number | null | undefined,
): boolean {
  return (
    typeof retryAfterSec === "number" &&
    Number.isFinite(retryAfterSec) &&
    retryAfterSec > MAX_RETRY_AFTER
  );
}
