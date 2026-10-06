import { describe, expect, it } from "vitest";

import {
  contextBudgetLabel,
  contextBudgetSteps,
  effectiveContextWindow,
} from "../contextBudget";

describe("contextBudget", () => {
  it("hides the control at 128K and offers steps under a longer window", () => {
    expect(contextBudgetSteps(null)).toBeNull();
    expect(contextBudgetSteps(128_000)).toBeNull();
    expect(contextBudgetSteps(200_000)).toEqual([128_000, 200_000]);
    expect(contextBudgetSteps(1_000_000)).toEqual([
      128_000, 256_000, 512_000, 1_000_000,
    ]);
    expect(contextBudgetLabel(128_000)).toBe("128K");
    expect(contextBudgetLabel(1_000_000)).toBe("1M");
  });

  it("uses the shorter of the step and the catalog window", () => {
    expect(effectiveContextWindow(1_000_000, null)).toBe(1_000_000);
    expect(effectiveContextWindow(1_000_000, 128_000)).toBe(128_000);
    expect(effectiveContextWindow(128_000, 512_000)).toBe(128_000);
    expect(effectiveContextWindow(null, 128_000)).toBeNull();
  });
});
