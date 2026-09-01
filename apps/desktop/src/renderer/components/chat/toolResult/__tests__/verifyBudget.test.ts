import { describe, expect, it } from "vitest";
import { isVerifyBudgetExceeded, verifyIncompleteFace } from "../verifyBudget";

describe("isVerifyBudgetExceeded", () => {
  it("true only when display.budget_exceeded === true", () => {
    expect(isVerifyBudgetExceeded({ budget_exceeded: true })).toBe(true);
    expect(
      isVerifyBudgetExceeded({ budget_exceeded: true, timeout_kind: "idle" }),
    ).toBe(true);
    expect(isVerifyBudgetExceeded({ budget_exceeded: false })).toBe(false);
    expect(isVerifyBudgetExceeded({ timeout_kind: "idle" })).toBe(false);
    expect(isVerifyBudgetExceeded({ exit_code: -1 })).toBe(false);
    expect(isVerifyBudgetExceeded(null)).toBe(false);
    expect(isVerifyBudgetExceeded(undefined)).toBe(false);
  });
});

describe("verifyIncompleteFace", () => {
  it("idle / disaster / no kind", () => {
    expect(
      verifyIncompleteFace({
        budget_exceeded: true,
        timeout_kind: "idle",
      }),
    ).toBe("执行无响应（无输出已中止）");
    expect(
      verifyIncompleteFace({
        budget_exceeded: true,
        timeout_kind: "disaster",
      }),
    ).toBe("执行已强制中止");
    expect(verifyIncompleteFace({ budget_exceeded: true })).toBe("验证未完成");
    expect(
      verifyIncompleteFace({ budget_exceeded: true, timeout_kind: null }),
    ).toBe("验证未完成");
    expect(verifyIncompleteFace({ timeout_kind: "idle" })).toBe("");
  });
});
