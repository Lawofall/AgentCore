import { describe, expect, it } from "vitest";
import {
  CACHE_BILLED_AS_MISS_LABEL,
  cacheDisplayBilledAsMiss,
  cacheUsageDisplay,
  displayCacheMiss,
  isOmittedCacheSplit,
} from "./cacheUsageDisplay";

const omitted = { input: 800, cache_hit: 0, cache_miss: 0 };
const deepseekZero = { input: 800, cache_hit: 0, cache_miss: 800 };
const hitSplit = { input: 100, cache_hit: 20, cache_miss: 80 };

describe("omitted cache split (input>0 and 0/0)", () => {
  it("is the omitted shape and bills the whole prompt as miss", () => {
    expect(isOmittedCacheSplit(omitted)).toBe(true);
    expect(displayCacheMiss(omitted)).toBe(800);
    expect(cacheDisplayBilledAsMiss(omitted)).toBe(true);
    expect(cacheUsageDisplay(omitted)).toEqual({
      billedAsMiss: true,
      cacheHit: 0,
      cacheMiss: 800,
      hitRatePercent: null,
    });
  });

  it("copy states billing口径, not an upstream 0-hit", () => {
    expect(CACHE_BILLED_AS_MISS_LABEL).toBe("按未命中计价");
    expect(CACHE_BILLED_AS_MISS_LABEL).not.toMatch(/0|命中为 0|无缓存/);
  });
});

describe("DeepSeek true 0 hit (miss=input)", () => {
  it("does not rewrite the miss count", () => {
    expect(isOmittedCacheSplit(deepseekZero)).toBe(false);
    expect(displayCacheMiss(deepseekZero)).toBe(800);
    expect(cacheUsageDisplay(deepseekZero).cacheMiss).toBe(800);
    expect(cacheUsageDisplay(deepseekZero).cacheHit).toBe(0);
  });
});

describe("reported hit/miss split", () => {
  it("leaves numbers and hit-rate display alone", () => {
    expect(isOmittedCacheSplit(hitSplit)).toBe(false);
    expect(cacheDisplayBilledAsMiss(hitSplit)).toBe(false);
    expect(displayCacheMiss(hitSplit)).toBe(80);
    expect(cacheUsageDisplay(hitSplit)).toEqual({
      billedAsMiss: false,
      cacheHit: 20,
      cacheMiss: 80,
      hitRatePercent: 20,
    });
  });

  it("does not fill miss when hit>0 and miss is 0", () => {
    const partial = { input: 100, cache_hit: 60, cache_miss: 0 };
    expect(displayCacheMiss(partial)).toBe(0);
    expect(cacheDisplayBilledAsMiss(partial)).toBe(false);
  });
});
