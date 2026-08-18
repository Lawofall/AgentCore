import type { ProcessStep } from "@agentcore/contract-types";
import { describe, expect, it } from "vitest";
import {
  exportDeliverableText,
  formatMessageExport,
  formatProcessExport,
} from "../messageExport";

const steps: ProcessStep[] = [
  { kind: "reasoning", text: "先查资料" },
  {
    kind: "tool",
    id: "t1",
    tool_name: "web_search",
    arguments: { query: "AgentCore" },
    result: "ok",
    status: "success",
  },
  { kind: "content", text: "我先看一下。" },
  { kind: "content", text: "最终方案如下。" },
];

describe("formatMessageExport (mobile)", () => {
  it("defaults to deliverable-only", () => {
    expect(formatMessageExport("最终方案如下。", steps, "deliverable")).toBe(
      "最终方案如下。",
    );
  });

  it("includes process without duplicating trailing deliverable", () => {
    const text = formatMessageExport("最终方案如下。", steps, "with_process");
    expect(text.startsWith("【过程】")).toBe(true);
    expect(text).toContain("Search web");
    expect(text).not.toContain("【交付】");
  });

  it("formats process tools", () => {
    expect(formatProcessExport(steps)).toContain("AgentCore");
  });

  it("does not export wait.reason as the tool line detail", () => {
    expect(
      formatProcessExport([
        {
          kind: "tool",
          id: "w1",
          tool_name: "wait",
          arguments: { reason: "学术视角研究员仍在跑…" },
          result: null,
          status: "success",
        },
      ]),
    ).toBe("· Wait");
  });

  it("exports rework in completed tense (copy is not live)", () => {
    expect(
      formatProcessExport([
        { kind: "rework" },
        { kind: "content", text: "新稿" },
      ]),
    ).toContain("（引用/格式核验后已重写）");
    expect(formatProcessExport([{ kind: "rework" }])).not.toContain(
      "正在按规则修订",
    );
  });

  it("uses failure notice when content is empty (pure failure export)", () => {
    expect(
      formatMessageExport("", undefined, "deliverable", {
        failureNotice: "API Key 已吊销，请重新配置。",
      }),
    ).toBe("API Key 已吊销，请重新配置。");
  });

  it("prefers non-empty content over failure notice (no content===error hide)", () => {
    expect(exportDeliverableText("半成品", "后面挂了的错因")).toBe("半成品");
    expect(
      formatMessageExport("半成品", undefined, "deliverable", {
        failureNotice: "后面挂了的错因",
      }),
    ).toBe("半成品");
  });
});
