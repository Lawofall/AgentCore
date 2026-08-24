import { describe, expect, it } from "vitest";
import {
  GENERIC_TOOL_FAILURE_MESSAGE,
  specificToolFailureMessage,
} from "../productFailureFace";

describe("specificToolFailureMessage", () => {
  it("hides the unclassified default", () => {
    expect(
      specificToolFailureMessage({
        status: "error",
        failure: { message: GENERIC_TOOL_FAILURE_MESSAGE },
      }),
    ).toBeNull();
  });

  it("hides empty cousins", () => {
    expect(
      specificToolFailureMessage({
        status: "error",
        failure: {
          message: "这一步没能用上合适的工具，已跳过；我会换个方式继续。",
        },
      }),
    ).toBeNull();
    expect(
      specificToolFailureMessage({
        status: "error",
        failure: { message: "未找到所需资源，请换一种方式继续。" },
      }),
    ).toBeNull();
  });

  it("keeps a cause-specific sentence", () => {
    expect(
      specificToolFailureMessage({
        status: "error",
        failure: { message: "等待队员超时。" },
      }),
    ).toBe("等待队员超时。");
  });

  it("is silent on success", () => {
    expect(
      specificToolFailureMessage({
        status: "success",
        failure: { message: "等待队员超时。" },
      }),
    ).toBeNull();
  });
});
