/**
 * Display projection of the prompt-cache split.
 *
 * Wire 0/0 with input>0 is「上游省略了拆分」(e.g. BYOK gpt-5.6-sol). Pricing already
 * bills that as a full miss (`max(input − hit, miss)`). Display follows the same
 * guard and states our billing口径 — never「0 命中 / 无缓存」, which would assert
 * an upstream hit of 0. DeepSeek true-zero (`miss === input`) is unchanged
 * numerically; after REST projection the omitted shape is also 0/input, so the
 * billing copy covers both.
 */

export const CACHE_BILLED_AS_MISS_LABEL = "按未命中计价";

export type CacheSplitCounts = {
  input: number;
  cache_hit: number;
  cache_miss: number;
};

export function isOmittedCacheSplit(counts: CacheSplitCounts): boolean {
  return counts.input > 0 && counts.cache_hit === 0 && counts.cache_miss === 0;
}

/** Same guard as server `reconcile_cache_miss_tokens` / `calculate_cost`. */
export function displayCacheMiss(counts: CacheSplitCounts): number {
  if (isOmittedCacheSplit(counts)) {
    return Math.max(counts.input - counts.cache_hit, counts.cache_miss);
  }
  return counts.cache_miss;
}

/**
 * Paint billing口径 instead of「0 命中」: omitted 0/0 (live SSE) or a full-miss
 * split (DeepSeek true zero, or REST projection of the omitted shape).
 */
export function cacheDisplayBilledAsMiss(counts: CacheSplitCounts): boolean {
  if (counts.input <= 0 || counts.cache_hit !== 0) return false;
  return counts.cache_miss === 0 || counts.cache_miss === counts.input;
}

export function cacheUsageDisplay(counts: CacheSplitCounts): {
  billedAsMiss: boolean;
  cacheHit: number;
  cacheMiss: number;
  hitRatePercent: number | null;
} {
  const billedAsMiss = cacheDisplayBilledAsMiss(counts);
  return {
    billedAsMiss,
    cacheHit: counts.cache_hit,
    cacheMiss: displayCacheMiss(counts),
    hitRatePercent:
      counts.input > 0 && !billedAsMiss
        ? Math.round((counts.cache_hit / counts.input) * 100)
        : null,
  };
}
