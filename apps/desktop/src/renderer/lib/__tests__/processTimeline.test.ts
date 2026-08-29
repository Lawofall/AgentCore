import {
  PROCESS_STEP_KIND,
  type TimelineNode,
  appendContentStep,
  appendStageCardStep,
  appendUserInterjectionStep,
  dropTrailingContentSteps,
  groupToolRuns,
  hasClosedBlockWithText,
  isOrchestrationTool,
  isWaitIdleReasoning,
  omitCoordinationIdleSteps,
  promoteScalarContentIntoProcess,
  replaceTrailingContentStep,
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
  tool_name = "file_read",
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
      tool("a", "file_read", "success"),
      tool("b", "str_replace", "error"),
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
      "file_read",
      "str_replace",
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

  it("skips leftover retired ask / team_preview steps (not in the render union)", () => {
    const nodes = groupToolRuns([
      content("导语"),
      leftoverTeamPreview("tp1"),
      { kind: "ask", checkpoint_id: "old" } as unknown as ProcessStep,
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
    expect(isOrchestrationTool("file_read")).toBe(false);
    expect(isOrchestrationTool("web_search")).toBe(false);
    expect(isOrchestrationTool("")).toBe(false);
  });
});

describe("isWaitIdleReasoning / omitCoordinationIdleSteps (S4)", () => {
  it("marks reasoning followed only by wait as idle", () => {
    const process = [
      reasoning("无需处置"),
      tool("w1", "wait"),
      content("旁白"),
    ];
    expect(isWaitIdleReasoning(process, 0)).toBe(true);
    expect(isWaitIdleReasoning(process, 2)).toBe(false);
  });

  it("marks trailing reasoning after wait as idle (live wait-loop)", () => {
    const process = [tool("w1", "wait"), reasoning("继续听团")];
    expect(isWaitIdleReasoning(process, 1)).toBe(true);
  });

  it("does not mark reasoning before a real tool as idle", () => {
    const process = [reasoning("要读文件"), tool("a", "file_read")];
    expect(isWaitIdleReasoning(process, 0)).toBe(false);
  });

  it("does not mark the first open reasoning (no wait neighbor) as idle", () => {
    expect(isWaitIdleReasoning([reasoning("开场想一下")], 0)).toBe(false);
  });

  it("omits wait tools and idle reasoning; keeps content / real tools", () => {
    const process = [
      reasoning("派活"),
      tool("d1", "file_read"),
      reasoning("空等"),
      tool("w1", "wait"),
      tool("w2", "wait"),
      reasoning("仍空等"),
      content("对用户说一句"),
      reasoning("收尾想"),
      tool("a", "update_synthesis"),
    ];
    expect(omitCoordinationIdleSteps(process)).toEqual([
      reasoning("派活"),
      tool("d1", "file_read"),
      content("对用户说一句"),
      reasoning("收尾想"),
      tool("a", "update_synthesis"),
    ]);
  });

  it("returns the same reference when nothing is idle", () => {
    const process = [reasoning("想"), tool("a"), content("答")];
    expect(omitCoordinationIdleSteps(process)).toBe(process);
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

describe("appendStageCardStep", () => {
  it("drops a stage_card marker and dedupes by id", () => {
    const once = appendStageCardStep([content("调研呈报")], "sc1");
    expect(once).toEqual([
      { kind: "content", text: "调研呈报" },
      { kind: "stage_card", stage_card_id: "sc1" },
    ]);
    expect(appendStageCardStep(once, "sc1")).toBe(once);
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

describe("PROCESS_STEP_KIND", () => {
  it("registers every current ProcessStep kind (compile-time Record; runtime mirror)", () => {
    expect(PROCESS_STEP_KIND.reasoning).toBe(true);
    expect(PROCESS_STEP_KIND.user_interjection).toBe(true);
    expect(Object.keys(PROCESS_STEP_KIND).length).toBeGreaterThan(10);
  });
});
