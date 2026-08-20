import { describe, expect, it } from "vitest";
import {
  HANGING_QUESTION_CAPTION,
  HANGING_QUESTION_CTA,
  HANGING_QUESTION_DEFAULT_HINT,
  HANGING_QUESTION_DETACHED_HINT,
  formatHangingDefault,
} from "./hangingQuestion";

describe("hanging question copy", () => {
  it("does not reuse paused-checkpoint caption or CTA", () => {
    expect(HANGING_QUESTION_CAPTION).toBe("有事等你，团队照跑");
    expect(HANGING_QUESTION_CTA).toBe("答复");
    expect(HANGING_QUESTION_CAPTION).not.toMatch(/拍板|挂起|停工|暂停/);
    expect(HANGING_QUESTION_CTA).not.toBe("提交");
  });

  it("keeps the detached-graph hint honest", () => {
    expect(HANGING_QUESTION_DETACHED_HINT).toContain("新消息");
    expect(HANGING_QUESTION_DETACHED_HINT).toContain("接不上");
  });

  it("formats a default-continues hint from assumptions", () => {
    expect(
      formatHangingDefault([{ id: "a1", label: "格式", value: "仅 Markdown" }]),
    ).toBe(`${HANGING_QUESTION_DEFAULT_HINT}：格式：仅 Markdown`);
    expect(formatHangingDefault([])).toBeNull();
    expect(formatHangingDefault(undefined)).toBeNull();
  });
});
