import type { ProcessStep } from "@/types/events";
import { describe, expect, it } from "vitest";
import {
  collectFailedToolNames,
  formatUnproductiveToolFailureHint,
  shouldShowUnproductiveToolFailureHint,
} from "../unproductiveToolFailureHint";

function toolStep(
  tool_name: string,
  status: "running" | "success" | "error" | "redirect",
  id = tool_name,
): Extract<ProcessStep, { kind: "tool" }> {
  return {
    kind: "tool",
    id,
    tool_name,
    arguments: {},
    result: status === "error" ? "failed" : null,
    status,
  };
}

describe("collectFailedToolNames", () => {
  it("collects status=error tool rows from process", () => {
    expect(
      collectFailedToolNames([
        toolStep("host_shell", "error"),
        toolStep("web_search", "success"),
        toolStep("host_apps", "error", "host_apps-2"),
      ]),
    ).toEqual(["host_shell", "host_apps"]);
  });

  it("returns empty when process has no error tools", () => {
    expect(collectFailedToolNames([toolStep("host_shell", "success")])).toEqual(
      [],
    );
    expect(collectFailedToolNames(undefined)).toEqual([]);
  });

  it("omits channel-redirect rows even when journaled as error", () => {
    expect(
      collectFailedToolNames([
        {
          kind: "tool",
          id: "c1",
          tool_name: "code_execute",
          arguments: {},
          result: "禁止用 code_execute 打开源码再正则扫描。",
          status: "error",
          failure: {
            message: "这一步想用脚本打开源码再搜索，没有执行。",
            code: "source_grep_redirect",
          },
        },
      ]),
    ).toEqual([]);
  });

  it("falls back to journal.runProcesses only when process has no failures", () => {
    expect(
      collectFailedToolNames([toolStep("host_shell", "success")], {
        events: [],
        finishReason: "unproductive",
        runProcesses: {
          run_a: [toolStep("host_apps", "error")],
        },
      }),
    ).toEqual(["host_apps"]);

    expect(
      collectFailedToolNames([toolStep("host_shell", "error")], {
        events: [],
        finishReason: "unproductive",
        runProcesses: {
          run_a: [toolStep("host_apps", "error")],
        },
      }),
    ).toEqual(["host_shell"]);
  });
});

describe("shouldShowUnproductiveToolFailureHint", () => {
  it("shows for content + unproductive + failed tools", () => {
    expect(
      shouldShowUnproductiveToolFailureHint({
        finishReason: "unproductive",
        content: "已改好菜单。",
        failedToolNames: ["host_shell"],
      }),
    ).toBe(true);
  });

  it("hides when empty content (existing failure card path)", () => {
    expect(
      shouldShowUnproductiveToolFailureHint({
        finishReason: "unproductive",
        content: "   ",
        failedToolNames: ["host_shell"],
      }),
    ).toBe(false);
  });

  it("hides without failed tools or non-unproductive finish", () => {
    expect(
      shouldShowUnproductiveToolFailureHint({
        finishReason: "unproductive",
        content: "ok",
        failedToolNames: [],
      }),
    ).toBe(false);
    expect(
      shouldShowUnproductiveToolFailureHint({
        finishReason: "end_turn",
        content: "ok",
        failedToolNames: ["host_shell"],
      }),
    ).toBe(false);
  });
});

describe("formatUnproductiveToolFailureHint", () => {
  it("formats count + labels in human short copy", () => {
    expect(formatUnproductiveToolFailureHint(["host_shell"])).toBe(
      "本轮有 1 个工具未成功：host_shell",
    );
    expect(
      formatUnproductiveToolFailureHint(["host_shell", "host_apps"], (n) =>
        n === "host_shell" ? "Host shell" : "Host apps",
      ),
    ).toBe("本轮有 2 个工具未成功：Host shell、Host apps");
  });

  it("dedupes labels but keeps failure count", () => {
    expect(
      formatUnproductiveToolFailureHint(["host_shell", "host_shell"]),
    ).toBe("本轮有 2 个工具未成功：host_shell");
  });

  it("returns null for empty", () => {
    expect(formatUnproductiveToolFailureHint([])).toBeNull();
  });
});
