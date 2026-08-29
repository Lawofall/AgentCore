import type { ProcessStep } from "@agentcore/contract-types";
import { describe, expect, it } from "vitest";
import { formatMessageExport, formatProcessExport } from "../messageExport";

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

describe("formatProcessExport", () => {
  it("orders narration and tool lines for copy", () => {
    const text = formatProcessExport(steps);
    expect(text).toContain("【思考】");
    expect(text).toContain("先查资料");
    expect(text).toContain("Search web");
    expect(text).toContain("AgentCore");
    expect(text).toContain("我先看一下。");
    expect(text).toContain("最终方案如下。");
  });

  it("returns empty for missing process", () => {
    expect(formatProcessExport(undefined)).toBe("");
    expect(formatProcessExport([])).toBe("");
  });

  it("复制出去的过程稿不摆内部标识（与工具行标题同一条纪律）", () => {
    const coordination: ProcessStep[] = [
      {
        kind: "tool",
        id: "t1",
        tool_name: "cancel_worker",
        arguments: { run_id: "r-a3f2e1c8-9b21", reason: "方向跑偏" },
        result: "ok",
        status: "success",
      },
    ];
    const text = formatProcessExport(coordination);
    expect(text).not.toContain("r-a3f2e1c8-9b21");
    expect(text).toContain("方向跑偏");
  });

  it("does not export wait.reason; labels the row Wait", () => {
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

  it("uses English chrome for list_folders / git subcommand, not snake_case or commit headline", () => {
    expect(
      formatProcessExport([
        {
          kind: "tool",
          id: "t1",
          tool_name: "list_folders",
          arguments: {},
          result: "ok",
          status: "success",
        },
      ]),
    ).toBe("· List folders");
    const git = formatProcessExport([
      {
        kind: "tool",
        id: "t2",
        tool_name: "git",
        arguments: {
          subcommand: "commit",
          message: "feat: fold process tools into one line",
        },
        result: "ok",
        status: "success",
      },
    ]);
    expect(git).toContain("Git");
    expect(git).toContain("commit");
    expect(git).not.toContain("feat: fold process tools");
    expect(git).not.toContain("list_folders");
  });
});

describe("formatMessageExport", () => {
  it("defaults to deliverable-only", () => {
    expect(formatMessageExport("最终方案如下。", steps, "deliverable")).toBe(
      "最终方案如下。",
    );
  });

  it("includes process without duplicating trailing deliverable", () => {
    const text = formatMessageExport("最终方案如下。", steps, "with_process");
    expect(text.startsWith("【过程】")).toBe(true);
    expect(text).toContain("我先看一下。");
    expect(text).toContain("最终方案如下。");
    expect(text).not.toContain("【交付】");
  });

  it("appends deliverable when timeline lacks it", () => {
    const processOnly: ProcessStep[] = [
      { kind: "reasoning", text: "想一下" },
      {
        kind: "tool",
        id: "t1",
        tool_name: "grep",
        arguments: { pattern: "foo" },
        result: null,
        status: "success",
      },
    ];
    const text = formatMessageExport("交付正文", processOnly, "with_process");
    expect(text).toContain("【过程】");
    expect(text).toContain("【交付】");
    expect(text).toContain("交付正文");
  });

  it("falls back to deliverable when process is empty", () => {
    expect(formatMessageExport("仅交付", undefined, "with_process")).toBe(
      "仅交付",
    );
  });

  it("deliverable-only uses error.message on empty failure", () => {
    expect(
      formatMessageExport("", undefined, "deliverable", {
        error: { message: "模型调用失败，请重试。" },
      }),
    ).toBe("模型调用失败，请重试。");
  });

  it("deliverable-only prefers content over error", () => {
    expect(
      formatMessageExport("已写出一半", undefined, "deliverable", {
        error: { message: "模型调用失败，请重试。" },
      }),
    ).toBe("已写出一半");
  });

  it("with_process empty failure still surfaces error as deliverable", () => {
    const processOnly: ProcessStep[] = [{ kind: "reasoning", text: "想一下" }];
    const text = formatMessageExport("", processOnly, "with_process", {
      error: { message: "工具连续无有效进展或参数无效，请重试。" },
    });
    expect(text).toContain("【过程】");
    expect(text).toContain("【交付】");
    expect(text).toContain("工具连续无有效进展或参数无效，请重试。");
  });
});
