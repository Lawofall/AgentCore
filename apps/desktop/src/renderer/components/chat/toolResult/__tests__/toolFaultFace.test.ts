import { describe, expect, it } from "vitest";
import {
  isSelfExplanatoryLookupError,
  toolGroupFaultLabel,
  toolRowFaultLabel,
} from "../toolFaultFace";

describe("toolRowFaultLabel", () => {
  it("is silent on success, running, and redirect", () => {
    expect(
      toolRowFaultLabel({ tool_name: "run", status: "success" }),
    ).toBeNull();
    expect(
      toolRowFaultLabel({ tool_name: "run", status: "running" }),
    ).toBeNull();
    expect(
      toolRowFaultLabel({
        tool_name: "code_execute",
        status: "redirect",
        failure: { code: "source_grep_redirect" },
      }),
    ).toBeNull();
  });

  it("labels exec tools 未通过, including parsed test failure with exit 0", () => {
    expect(
      toolRowFaultLabel({
        tool_name: "run",
        status: "error",
        display: { stdout: "1 failed", stderr: "", exit_code: 0 },
      }),
    ).toBe("未通过");
    expect(
      toolRowFaultLabel({
        tool_name: "code_execute",
        status: "error",
        display: { stdout: "", stderr: "boom", exit_code: 1 },
      }),
    ).toBe("未通过");
  });

  it("labels missing files / unmatched edits 未找到", () => {
    expect(
      toolRowFaultLabel({
        tool_name: "file_read",
        status: "error",
        failure: { code: "not_found" },
      }),
    ).toBe("未找到");
    expect(
      toolRowFaultLabel({ tool_name: "str_replace", status: "error" }),
    ).toBe("未找到");
    expect(
      toolRowFaultLabel({
        tool_name: "browser_click",
        status: "error",
        failure: { code: "NOT_FOUND" },
      }),
    ).toBe("未找到");
  });

  it("treats file lookup misses as skipping the extra sentence", () => {
    expect(
      isSelfExplanatoryLookupError({
        tool_name: "file_read",
        status: "error",
        failure: { code: "not_found" },
      }),
    ).toBe(true);
    expect(
      isSelfExplanatoryLookupError({
        tool_name: "str_replace",
        status: "error",
      }),
    ).toBe(true);
    expect(
      isSelfExplanatoryLookupError({
        tool_name: "browser_click",
        status: "error",
        failure: { code: "NOT_FOUND" },
      }),
    ).toBe(false);
  });

  it("labels other faults 未完成, not verify-incomplete", () => {
    expect(
      toolRowFaultLabel({
        tool_name: "wait",
        status: "error",
        failure: { code: "WAIT_TIMEOUT" },
      }),
    ).toBe("未完成");
    expect(
      toolRowFaultLabel({
        tool_name: "test_run",
        status: "error",
        display: {
          check: "typecheck",
          exit_code: -1,
          stdout: "",
          stderr: "idle",
          budget_exceeded: true,
        },
      }),
    ).toBeNull();
  });
});

describe("toolGroupFaultLabel", () => {
  it("is silent when every child succeeded", () => {
    expect(
      toolGroupFaultLabel([
        { tool_name: "file_read", status: "success" },
        { tool_name: "run", status: "success" },
      ]),
    ).toBeNull();
  });

  it("repeats a single child word", () => {
    expect(
      toolGroupFaultLabel([
        { tool_name: "file_read", status: "success" },
        { tool_name: "run", status: "error" },
      ]),
    ).toBe("未通过");
  });

  it("uses 未完成 when kinds mix", () => {
    expect(
      toolGroupFaultLabel([
        { tool_name: "file_read", status: "error" },
        { tool_name: "run", status: "error" },
      ]),
    ).toBe("未完成");
  });
});
