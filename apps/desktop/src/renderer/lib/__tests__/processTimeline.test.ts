import {
  PROCESS_STEP_KIND,
  type TimelineNode,
  appendContentStep,
  appendUserInterjectionStep,
  completeProcessTranscript,
  dropTrailingContentSteps,
  groupToolRuns,
  hasClosedBlockWithText,
  isOrchestrationTool,
  processFoldMask,
  processTurnLane,
  promoteScalarContentIntoProcess,
  replaceTrailingContentStep,
  stitchAssistantProcess,
  timelineNodeKeys,
} from "@/lib/processTimeline";
import type { ProcessStep } from "@/types/events";
import { describe, expect, it } from "vitest";

describe("hasClosedBlockWithText", () => {
  const block = "收到，按完整官网来做。我这就派团队开工。";
  const closed: ProcessStep[] = [
    { kind: "content", text: "开场白。" },
    { kind: "content", text: block },
    { kind: "reasoning", text: "接着编排" },
    { kind: "team", execution_id: "e1" },
  ];

  it("is true when a closed content block already matches", () => {
    expect(hasClosedBlockWithText(closed, "content", block)).toBe(true);
  });

  it("is false while that kind is still the open trailing step", () => {
    expect(
      hasClosedBlockWithText(
        [
          { kind: "content", text: "开场白。" },
          { kind: "content", text: block },
        ],
        "content",
        block,
      ),
    ).toBe(false);
  });

  it("append/replace keep the same process when the closed block is resent", () => {
    expect(appendContentStep(closed, block)).toBe(closed);
    expect(replaceTrailingContentStep(closed, block)).toBe(closed);
  });
});

describe("appendUserInterjectionStep", () => {
  it("appends once per interjection id and dedupes later calls", () => {
    const once = appendUserInterjectionStep(
      [{ kind: "content", text: "你好" }],
      "inj-1",
    );
    expect(once).toEqual([
      { kind: "content", text: "你好" },
      { kind: "user_interjection", interjection_id: "inj-1" },
    ]);
    expect(appendUserInterjectionStep(once, "inj-1")).toBe(once);
  });

  it("keys timeline nodes by interjection id", () => {
    const nodes = groupToolRuns([
      { kind: "content", text: "a" },
      { kind: "user_interjection", interjection_id: "inj-1" },
      { kind: "content", text: "b" },
    ]);
    expect(timelineNodeKeys(nodes)).toEqual([
      "content-1",
      "inj-inj-1",
      "content-2",
    ]);
  });
});

const reasoning = (text: string): ProcessStep => ({ kind: "reasoning", text });
const content = (text: string): ProcessStep => ({ kind: "content", text });
const team = (execution_id: string): ProcessStep => ({
  kind: "team",
  execution_id,
});
const leftoverTeamPreview = (checkpoint_id: string): ProcessStep =>
  ({ kind: "team_preview", checkpoint_id }) as unknown as ProcessStep;
const tool = (
  id: string,
  tool_name = "read",
  status: "running" | "success" | "error" = "success",
): ProcessStep => ({
  kind: "tool",
  id,
  tool_name,
  arguments: {},
  result: null,
  status,
});

describe("groupToolRuns", () => {
  it("returns [] for an empty timeline", () => {
    expect(groupToolRuns([])).toEqual([]);
  });

  it("folds a run of ≥2 consecutive tools into one tool-group, in order", () => {
    const nodes = groupToolRuns([tool("a"), tool("b"), tool("c")]);
    expect(nodes).toHaveLength(1);
    const group = nodes[0];
    expect(group.kind).toBe("tool-group");
    if (group.kind !== "tool-group") throw new Error("expected tool-group");
    expect(group.tools.map((t) => t.id)).toEqual(["a", "b", "c"]);
  });

  it("keeps a lone tool inline (threshold ≥2 — singles are not wrapped)", () => {
    const nodes = groupToolRuns([tool("a")]);
    expect(nodes).toHaveLength(1);
    expect(nodes[0]).toMatchObject({ kind: "tool", step: { id: "a" } });
  });

  it("breaks runs on reasoning/content boundaries, preserving chronology", () => {
    const nodes = groupToolRuns([
      reasoning("想一下"),
      tool("a"),
      tool("b"),
      reasoning("再想"),
      tool("c"),
      content("答案"),
    ]);
    expect(nodes.map((n) => n.kind)).toEqual([
      "reasoning",
      "tool-group", // a + b
      "reasoning",
      "tool", // lone c stays inline
      "content",
    ]);
    const group = nodes[1];
    if (group.kind !== "tool-group") throw new Error("expected tool-group");
    expect(group.tools.map((t) => t.id)).toEqual(["a", "b"]);
  });

  it("never folds the trailing content (final answer) into a group", () => {
    const nodes = groupToolRuns([tool("a"), tool("b"), content("最终答案")]);
    const last = nodes[nodes.length - 1] as TimelineNode;
    expect(last.kind).toBe("content");
    if (last.kind !== "content") throw new Error("expected content");
    expect(last.text).toBe("最终答案");
  });

  it("preserves per-step status inside a group (mixed running/success/error)", () => {
    const nodes = groupToolRuns([
      tool("a", "read", "success"),
      tool("b", "edit", "error"),
      tool("c", "file_list", "running"),
    ]);
    const group = nodes[0];
    if (group.kind !== "tool-group") throw new Error("expected tool-group");
    expect(group.tools.map((t) => t.status)).toEqual([
      "success",
      "error",
      "running",
    ]);
    expect(group.tools.map((t) => t.tool_name)).toEqual([
      "read",
      "edit",
      "file_list",
    ]);
  });

  it("folds two separate runs split by content into two nodes", () => {
    const nodes = groupToolRuns([
      tool("a"),
      tool("b"),
      content("中间正文"),
      tool("c"),
      tool("d"),
    ]);
    expect(nodes.map((n) => n.kind)).toEqual([
      "tool-group",
      "content",
      "tool-group",
    ]);
  });

  it("does not mutate the input array", () => {
    const process: ProcessStep[] = [tool("a"), tool("b")];
    const snapshot = [...process];
    groupToolRuns(process);
    expect(process).toEqual(snapshot);
  });

  it("keeps a `team` marker as its own boundary node (graph slots at its position)", () => {
    // 统一团队时间线: an orchestration call no longer emits a tool step — a `team` marker
    // stands in its place, and the collaboration graph renders AT this node's position.
    const nodes = groupToolRuns([tool("a"), team("exec1"), tool("b")]);
    expect(nodes.map((n) => n.kind)).toEqual(["tool", "team", "tool"]);
    const mid = nodes[1];
    if (mid.kind !== "team") throw new Error("expected team");
    expect(mid.execution_id).toBe("exec1");
  });

  it("breaks tool runs around a `team` marker (does not absorb it into a group)", () => {
    const nodes = groupToolRuns([
      tool("a"),
      tool("b"),
      team("exec1"),
      tool("c"),
      tool("e"),
    ]);
    // a+b group · team lone boundary · c+e group
    expect(nodes.map((n) => n.kind)).toEqual([
      "tool-group",
      "team",
      "tool-group",
    ]);
  });

  it("skips leftover retired ask / team_preview / stage_card / plan_review steps (not in the render union)", () => {
    const leftoverStageCard = (stage_card_id: string): ProcessStep =>
      ({ kind: "stage_card", stage_card_id }) as unknown as ProcessStep;
    const leftoverPlanReview = (checkpoint_id: string): ProcessStep =>
      ({ kind: "plan_review", checkpoint_id }) as unknown as ProcessStep;
    const nodes = groupToolRuns([
      content("导语"),
      leftoverTeamPreview("tp1"),
      leftoverPlanReview("pr1"),
      { kind: "ask", checkpoint_id: "old" } as unknown as ProcessStep,
      leftoverStageCard("sc1"),
      team("exec1"),
    ]);
    expect(nodes.map((n) => n.kind)).toEqual(["content", "team"]);
  });
});

describe("isOrchestrationTool", () => {
  it("flags delegate and debate as team-handoff tools", () => {
    expect(isOrchestrationTool("delegate")).toBe(true);
    expect(isOrchestrationTool("debate")).toBe(true);
  });

  it("is false for ordinary read/write tools", () => {
    expect(isOrchestrationTool("read")).toBe(false);
    expect(isOrchestrationTool("web_search")).toBe(false);
    expect(isOrchestrationTool("")).toBe(false);
  });
});

describe("dropTrailingContentSteps", () => {
  it("returns [] for empty / undefined", () => {
    expect(dropTrailingContentSteps([])).toEqual([]);
    expect(dropTrailingContentSteps(undefined)).toEqual([]);
  });

  it("drops the trailing content step (回炉丢弃草稿正文)", () => {
    expect(
      dropTrailingContentSteps([reasoning("想一下"), content("草稿答案")]),
    ).toEqual([reasoning("想一下")]);
  });

  it("keeps preceding reasoning / tool steps (它们真实发生过)", () => {
    expect(
      dropTrailingContentSteps([reasoning("想"), tool("a"), content("草稿")]),
    ).toEqual([reasoning("想"), tool("a")]);
  });

  it("pops all consecutive trailing content steps", () => {
    expect(
      dropTrailingContentSteps([tool("a"), content("一"), content("二")]),
    ).toEqual([tool("a")]);
  });

  it("no-ops (same reference) when the last step is not content", () => {
    const process: ProcessStep[] = [content("正文"), tool("a")];
    expect(dropTrailingContentSteps(process)).toBe(process);
  });

  it("does not mutate the input array", () => {
    const process: ProcessStep[] = [reasoning("想"), content("草稿")];
    const snapshot = [...process];
    dropTrailingContentSteps(process);
    expect(process).toEqual(snapshot);
  });
});

describe("promoteScalarContentIntoProcess", () => {
  it("inserts scalar CEO lead-in before the first team marker", () => {
    expect(
      promoteScalarContentIntoProcess(
        [reasoning("想"), team("exec1")],
        "这是个很有意思的方向",
      ),
    ).toEqual([
      reasoning("想"),
      content("这是个很有意思的方向"),
      team("exec1"),
    ]);
  });

  it("no-ops when a content step already exists (same ref)", () => {
    const process = [content("导语"), team("exec1")];
    expect(promoteScalarContentIntoProcess(process, "导语")).toBe(process);
  });

  it("appends when there is no team marker yet", () => {
    expect(promoteScalarContentIntoProcess([reasoning("想")], "导语")).toEqual([
      reasoning("想"),
      content("导语"),
    ]);
  });
});

describe("completeProcessTranscript", () => {
  it("appends deliverable remainder after tools when process already has narration", () => {
    expect(
      completeProcessTranscript(
        [
          content("三份核查都已交回。现在我核对条文原文。"),
          tool("t1", "web_search"),
        ],
        "三份核查都已交回。现在我核对条文原文。\n\n正式备忘录已写好。",
      ),
    ).toEqual([
      content("三份核查都已交回。现在我核对条文原文。"),
      tool("t1", "web_search"),
      content("正式备忘录已写好。"),
    ]);
  });

  it("no-ops when extra text already equals a content step", () => {
    const process = [content("终稿")];
    expect(completeProcessTranscript(process, "终稿")).toBe(process);
  });

  it("extends a trailing open content step", () => {
    expect(
      completeProcessTranscript([content("现在落")], "现在落正式备忘录。"),
    ).toEqual([content("现在落正式备忘录。")]);
  });
});

describe("stitchAssistantProcess", () => {
  it("folds leftover content onto a cached assistant row", () => {
    expect(
      stitchAssistantProcess({
        role: "assistant" as const,
        content: "三份核查都已交回。现在我核对条文原文。\n\n正式备忘录已写好。",
        process: [
          content("三份核查都已交回。现在我核对条文原文。"),
          tool("t1", "web_search"),
        ],
      }).process,
    ).toEqual([
      content("三份核查都已交回。现在我核对条文原文。"),
      tool("t1", "web_search"),
      content("正式备忘录已写好。"),
    ]);
  });

  it("no-ops user rows", () => {
    const row = { role: "user" as const, content: "hi", process: undefined };
    expect(stitchAssistantProcess(row)).toBe(row);
  });
});

describe("PROCESS_STEP_KIND", () => {
  it("registers every current ProcessStep kind (compile-time Record; runtime mirror)", () => {
    expect(PROCESS_STEP_KIND.reasoning).toBe(true);
    expect(PROCESS_STEP_KIND.user_interjection).toBe(true);
    expect("plan_review" in PROCESS_STEP_KIND).toBe(false);
    expect(Object.keys(PROCESS_STEP_KIND).length).toBe(8);
  });
});

describe("processFoldMask · 正文不进过程折", () => {
  const checkpoint = (checkpoint_id: string): ProcessStep => ({
    kind: "checkpoint",
    checkpoint_id,
  });
  const leftoverPlanReview = (checkpoint_id: string): ProcessStep =>
    ({ kind: "plan_review", checkpoint_id }) as unknown as ProcessStep;
  const leftoverUnknown = (execution_id: string): ProcessStep =>
    ({
      kind: "retired_unknown",
      execution_id,
    }) as unknown as ProcessStep;

  function maskOf(process: ProcessStep[], pending: string[] = []) {
    return processFoldMask(groupToolRuns(process), new Set(pending));
  }

  it("keeps mid-content and the trailing answer out of the fold", () => {
    expect(
      maskOf([content("我先找日志"), tool("a"), content("清晰度是 1080p")]),
    ).toEqual([false, true, false]);
  });

  it("keeps content visible when the timeline ends on a tool", () => {
    expect(maskOf([content("还在找"), tool("a")])).toEqual([false, true]);
  });

  it("keeps CEO lead-in before the team graph", () => {
    expect(maskOf([content("我派了设计师"), team("e1")])).toEqual([
      false,
      false,
    ]);
  });

  it("keeps lead-in and the final answer after the graph", () => {
    expect(
      maskOf([content("先派出去"), team("e1"), content("结论：用方案 B")]),
    ).toEqual([false, false, false]);
  });

  it("keeps content immediately before a pending checkpoint", () => {
    expect(
      maskOf([content("你选哪个？"), checkpoint("cp1"), tool("a")], ["cp1"]),
    ).toEqual([false, false, true]);
  });

  it("keeps content before a resolved checkpoint (checkpoint still folds)", () => {
    expect(
      maskOf([content("你选哪个？"), checkpoint("cp1"), tool("a")]),
    ).toEqual([false, true, true]);
  });

  it("keeps a pending checkpoint out of the fold", () => {
    expect(
      maskOf([reasoning("想"), checkpoint("cp1"), content("答案")], ["cp1"]),
    ).toEqual([true, false, false]);
  });

  it("folds a resolved checkpoint with the trailing answer out", () => {
    expect(
      maskOf([reasoning("想"), checkpoint("cp1"), content("答案")]),
    ).toEqual([true, true, false]);
  });

  it("skips leftover trailing plan_review", () => {
    expect(
      maskOf([tool("a"), content("请过目这份提纲"), leftoverPlanReview("pr1")]),
    ).toEqual([true, false]);
  });

  it("skips unknown process kinds", () => {
    expect(
      maskOf([tool("a"), content("最终答案"), leftoverUnknown("e1")]),
    ).toEqual([true, false]);
  });

  it("does not fold team / interjection / escalation", () => {
    expect(
      maskOf([
        team("e1"),
        { kind: "user_interjection", interjection_id: "inj-1" },
        { kind: "escalation", escalation_id: "esc-1" },
      ]),
    ).toEqual([false, false, false]);
  });

  it("folds reasoning, tool-groups, and weak decision traces", () => {
    expect(
      maskOf([
        reasoning("想"),
        tool("a"),
        tool("b"),
        { kind: "approval", approval_id: "ap1" },
        content("答案"),
      ]),
    ).toEqual([true, true, true, false]);
  });
});

describe("processTurnLane · 收起摘要是答案 caption", () => {
  it("puts every content step on the answer lane and process on chrome", () => {
    const nodes = groupToolRuns([
      { kind: "content", text: "我先找日志" },
      {
        kind: "tool",
        id: "t0",
        tool_name: "read",
        arguments: {},
        result: null,
        status: "success",
      },
      { kind: "content", text: "答案" },
    ]);
    const mask = processFoldMask(nodes);
    expect(nodes.map((n, i) => processTurnLane(n, mask[i] === true))).toEqual([
      "answer",
      "chrome",
      "answer",
    ]);
  });

  it("puts trailing content on the answer lane and process on chrome", () => {
    const nodes = groupToolRuns([
      { kind: "reasoning", text: "想" },
      {
        kind: "tool",
        id: "t1",
        tool_name: "read",
        arguments: {},
        result: null,
        status: "success",
      },
      { kind: "content", text: "答案" },
    ]);
    const mask = processFoldMask(nodes);
    expect(nodes.map((n, i) => processTurnLane(n, mask[i] === true))).toEqual([
      "chrome",
      "chrome",
      "answer",
    ]);
  });

  it("keeps team / pending checkpoint / interjection on the slot lane", () => {
    const nodes: TimelineNode[] = [
      { kind: "team", execution_id: "e1" },
      { kind: "checkpoint", checkpoint_id: "cp1" },
      { kind: "user_interjection", interjection_id: "inj-1" },
    ];
    const mask = processFoldMask(nodes, new Set(["cp1"]));
    expect(nodes.map((n, i) => processTurnLane(n, mask[i] === true))).toEqual([
      "slot",
      "slot",
      "slot",
    ]);
  });
});
