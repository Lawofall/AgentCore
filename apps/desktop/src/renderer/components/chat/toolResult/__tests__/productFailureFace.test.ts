import { describe, expect, it } from "vitest";
import {
  GENERIC_TOOL_FAILURE_MESSAGE,
  MISSING_PATH_USER_FACE,
  RETIRED_VERIFY_RESULT_MESSAGE,
  STR_REPLACE_NO_MATCH_USER_FACE,
  compactToolFailureFace,
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

  it("hides the retired verify-result aside", () => {
    expect(
      specificToolFailureMessage({
        status: "error",
        failure: { message: RETIRED_VERIFY_RESULT_MESSAGE },
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

describe("compactToolFailureFace", () => {
  it("hides the lookup-miss sentence the title already covers", () => {
    expect(
      compactToolFailureFace({
        status: "error",
        failure: { message: "没找到 web/CONVENTIONS.md，我会换个方式继续。" },
      }),
    ).toBeNull();
    expect(
      compactToolFailureFace({
        status: "error",
        failure: { message: MISSING_PATH_USER_FACE, code: "not_found" },
      }),
    ).toBeNull();
  });

  it("hides a leaked not_found receipt instead of inventing a fallback", () => {
    expect(
      compactToolFailureFace({
        status: "error",
        toolName: "file_read",
        failure: {
          message:
            "文件不存在：web/CONVENTIONS.md\n可换 glob 更宽查找。勿反复重试。",
          code: "not_found",
        },
      }),
    ).toBeNull();
  });

  it("hides a leaked str_replace receipt", () => {
    expect(
      compactToolFailureFace({
        status: "error",
        toolName: "str_replace",
        failure: {
          message:
            "在 trialStore.ts 中找不到 old_string；请对照写回执重写精确锚。",
        },
      }),
    ).toBeNull();
    expect(
      compactToolFailureFace({
        status: "error",
        toolName: "str_replace",
        failure: { message: STR_REPLACE_NO_MATCH_USER_FACE },
      }),
    ).toBeNull();
  });
});
