import type { ProcessStep, RunDebrief } from "@/types/events";
import { describe, expect, it } from "vitest";
import {
  HANDOFF_RECEIPT,
  absorbHandoffBriefContent,
  debriefFromHandoffArgs,
} from "../handoffBrief";

function content(text: string): ProcessStep {
  return { kind: "content", text };
}

function handoff(
  args: Record<string, unknown> = {},
  status: "success" | "error" = "success",
): Extract<ProcessStep, { kind: "tool" }> {
  return {
    kind: "tool",
    id: "h1",
    tool_name: "handoff",
    arguments: args,
    result: status === "success" ? HANDOFF_RECEIPT : "失败",
    status,
  };
}

describe("absorbHandoffBriefContent", () => {
  it("folds trailing content into an empty successful handoff", () => {
    const out = absorbHandoffBriefContent([
      content("长文先交完。"),
      {
        kind: "tool",
        id: "w1",
        tool_name: "file_write",
        arguments: {},
        result: "ok",
        status: "success",
      },
      content("交叉验证完成，建议一周内表态。"),
      handoff({}),
    ]);
    expect(out).toHaveLength(3);
    expect(out[0]).toMatchObject({ kind: "content", text: "长文先交完。" });
    expect(out[1]).toMatchObject({ tool_name: "file_write" });
    expect(out[2]).toMatchObject({
      kind: "tool",
      tool_name: "handoff",
      arguments: { summary: "交叉验证完成，建议一周内表态。" },
    });
  });

  it("leaves legacy argument briefs and their preceding content alone", () => {
    const process: ProcessStep[] = [
      content("这是交付正文。"),
      handoff({ summary: "只给了结论一条", key_points: ["要点"] }),
    ];
    expect(absorbHandoffBriefContent(process)).toEqual(process);
  });

  it("fills from harvested debrief when content is missing", () => {
    const fallback: RunDebrief = {
      summary: "收获到的便条",
      key_points: null,
      assumptions: null,
      next_steps: null,
    };
    const out = absorbHandoffBriefContent([handoff({})], fallback);
    expect(
      debriefFromHandoffArgs(out[0].kind === "tool" ? out[0].arguments : {}),
    ).toMatchObject({
      summary: "收获到的便条",
    });
  });

  it("does not fold content into a failed handoff", () => {
    const process: ProcessStep[] = [content("便条"), handoff({}, "error")];
    expect(absorbHandoffBriefContent(process)).toEqual(process);
  });
});
