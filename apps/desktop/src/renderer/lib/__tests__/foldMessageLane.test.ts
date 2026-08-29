import {
  ensureTimelineMarkersFromJournal,
  foldCheckpointMarker,
  foldContentDelta,
  foldContentReset,
  foldReasoningDelta,
  foldTeamMarker,
  foldToolUseEnd,
  foldToolUsePhase,
  foldToolUseStart,
  messageLaneFromMessage,
} from "@/lib/foldMessageLane";
import type {
  ProcessStep,
  ToolUseEndPayload,
  ToolUseProgressPayload,
  ToolUseStartPayload,
} from "@/types/events";
import { describe, expect, it } from "vitest";

const startPayload = (
  over: Partial<ToolUseStartPayload> = {},
): ToolUseStartPayload => ({
  tool_call_id: "call_1",
  tool_name: "web_search",
  arguments: { query: "深圳天气" },
  ...over,
});

/** 一条通道的步骤拼接值——标量（`content` / `reasoning`）必须与它逐字相等。 */
const laneText = (
  process: ProcessStep[],
  kind: "content" | "reasoning",
): string => {
  let text = "";
  for (const step of process) {
    if (step.kind !== "content" && step.kind !== "reasoning") continue;
    if (step.kind === kind) text += step.text;
  }
  return text;
};

describe("foldMessageLane", () => {
  it("foldContentDelta appends content and process step", () => {
    const base = messageLaneFromMessage({ content: "hi" });
    const next = foldContentDelta(base, " there");
    expect(next.content).toBe("hi there");
    expect(next.process).toEqual([{ kind: "content", text: " there" }]);
  });

  it("foldContentReset(finish_guard) clears content without a process trace", () => {
    const base = messageLaneFromMessage({
      content: "bad draft",
      process: [
        { kind: "reasoning", text: "think" },
        { kind: "content", text: "bad draft" },
      ],
    });
    const next = foldContentReset(base, "finish_guard");
    expect(next.content).toBe("");
    expect(next.process).toEqual([{ kind: "reasoning", text: "think" }]);
  });

  // 所有 reason（含 finish_guard）只清正文、不折过程痕迹。
  it("foldContentReset(retry) clears content without a process trace", () => {
    const base = messageLaneFromMessage({
      content: "transient",
      process: [
        { kind: "reasoning", text: "think" },
        { kind: "content", text: "transient" },
      ],
    });
    const next = foldContentReset(base, "retry");
    expect(next.content).toBe("");
    expect(next.process).toEqual([{ kind: "reasoning", text: "think" }]);
  });

  it("foldReasoningDelta appends reasoning lane", () => {
    const base = messageLaneFromMessage({ content: "" });
    const next = foldReasoningDelta(base, "hmm");
    expect(next.reasoning).toBe("hmm");
    expect(next.process).toEqual([{ kind: "reasoning", text: "hmm" }]);
  });

  // attach 增量重放的帧级替换（`replace`）：重放段里「还没说完的那一步」带的是整步全文，
  // 追加会看到重复。换掉的只能是末尾那个尚未闭合的块——前面已闭合的步骤一个不动，
  // 且整路拼接的标量必须与步骤数组保持一致。
  it("foldContentDelta(replace) swaps the open content block, keeps settled steps", () => {
    const base = messageLaneFromMessage({
      content: "第一段结论。半句还没",
      process: [
        { kind: "content", text: "第一段结论。" },
        { kind: "reasoning", text: "再核一下" },
        {
          kind: "tool",
          id: "call_1",
          tool_name: "web_search",
          arguments: {},
          result: "ok",
          status: "success",
        },
        { kind: "content", text: "半句还没" },
      ],
    });
    const next = foldContentDelta(base, "半句还没说完，这是整步全文。", true);
    expect(next.process).toEqual([
      { kind: "content", text: "第一段结论。" },
      { kind: "reasoning", text: "再核一下" },
      {
        kind: "tool",
        id: "call_1",
        tool_name: "web_search",
        arguments: {},
        result: "ok",
        status: "success",
      },
      { kind: "content", text: "半句还没说完，这是整步全文。" },
    ]);
    expect(next.content).toBe("第一段结论。半句还没说完，这是整步全文。");
    expect(next.content).toBe(laneText(next.process, "content"));
  });

  // 通道上没有开放块（末步是工具 / 标记）→ 没什么可换，开新块并接上标量。
  it("foldContentDelta(replace) opens a new block when the lane has none open", () => {
    const base = messageLaneFromMessage({
      content: "开场白。",
      process: [
        { kind: "content", text: "开场白。" },
        { kind: "reasoning", text: "想一下" },
      ],
    });
    const next = foldContentDelta(base, "接着说。", true);
    expect(next.process).toEqual([
      { kind: "content", text: "开场白。" },
      { kind: "reasoning", text: "想一下" },
      { kind: "content", text: "接着说。" },
    ]);
    expect(next.content).toBe("开场白。接着说。");
    expect(next.content).toBe(laneText(next.process, "content"));
  });

  // Follow / attach catch-up resends a closed process_content snapshot after the
  // lane has moved on (reasoning / team). Opening a new block paints the same
  // paragraph twice — the live-only duplicate a hard refresh clears.
  it("foldContentDelta does not reopen a closed content block already on the lane", () => {
    const block =
      "收到，按**完整官网**来做。我这就派团队开工：多页展示型官网。";
    const base = messageLaneFromMessage({
      content: `开场白。${block}`,
      process: [
        { kind: "content", text: "开场白。" },
        { kind: "content", text: block },
        { kind: "reasoning", text: "接着编排" },
        { kind: "team", execution_id: "exec1" },
      ],
    });
    expect(foldContentDelta(base, block, true)).toBe(base);
    expect(foldContentDelta(base, block, false)).toBe(base);
  });

  it("foldReasoningDelta does not reopen a closed reasoning block already on the lane", () => {
    const block = "先对齐规模，再派视觉与内容。";
    const base = messageLaneFromMessage({
      content: "开场白。",
      reasoning: block,
      process: [
        { kind: "reasoning", text: block },
        { kind: "content", text: "开场白。" },
      ],
    });
    expect(foldReasoningDelta(base, block, true)).toBe(base);
    expect(foldReasoningDelta(base, block, false)).toBe(base);
  });

  it("foldReasoningDelta(replace) swaps the open reasoning block only", () => {
    const base = messageLaneFromMessage({
      content: "",
      reasoning: "先拆解。想到一半",
      process: [
        { kind: "reasoning", text: "先拆解。" },
        {
          kind: "tool",
          id: "call_1",
          tool_name: "web_search",
          arguments: {},
          result: "ok",
          status: "success",
        },
        { kind: "reasoning", text: "想到一半" },
      ],
    });
    const next = foldReasoningDelta(base, "想到一半，这是整步全文。", true);
    expect(next.process.map((s) => s.kind)).toEqual([
      "reasoning",
      "tool",
      "reasoning",
    ]);
    expect(next.process[0]).toEqual({ kind: "reasoning", text: "先拆解。" });
    expect(next.process[2]).toEqual({
      kind: "reasoning",
      text: "想到一半，这是整步全文。",
    });
    expect(next.reasoning).toBe("先拆解。想到一半，这是整步全文。");
    expect(next.reasoning).toBe(laneText(next.process, "reasoning"));
  });

  // 工具执行阶段进度 (联网搜索前端展示优化)
  it("foldToolUsePhase stamps a running tool step's phase", () => {
    const started = foldToolUseStart(
      messageLaneFromMessage({ content: "" }),
      startPayload(),
    );
    const next = foldToolUsePhase(started, {
      tool_call_id: "call_1",
      tool_name: "web_search",
      phase: "querying",
    } satisfies ToolUseProgressPayload);
    const step = next.process[0];
    expect(step.kind === "tool" && step.phase).toBe("querying");
  });

  it("foldToolUsePhase no-ops after the tool has ended (not running)", () => {
    const started = foldToolUseStart(
      messageLaneFromMessage({ content: "" }),
      startPayload(),
    );
    const ended = foldToolUseEnd(started, {
      tool_call_id: "call_1",
      tool_name: "web_search",
      result: "ok",
      status: "success",
    } as ToolUseEndPayload);
    const after = foldToolUsePhase(ended, {
      tool_call_id: "call_1",
      tool_name: "web_search",
      phase: "querying",
    } satisfies ToolUseProgressPayload);
    // Same reference (no-op) and no phase leaked onto the resolved step.
    expect(after).toBe(ended);
    const step = after.process[0];
    expect(step.kind === "tool" && step.phase).toBeUndefined();
  });

  it("foldToolUsePhase no-ops for a delegated worker's call (run_id)", () => {
    // A worker call never entered the captain timeline, so there is nothing to stamp.
    const base = messageLaneFromMessage({ content: "" });
    const after = foldToolUsePhase(base, {
      tool_call_id: "call_worker",
      tool_name: "web_search",
      phase: "querying",
      run_id: "run_2",
    } satisfies ToolUseProgressPayload);
    expect(after).toBe(base);
  });

  it("foldCheckpointMarker keeps trailing content (absorb is content_reset)", () => {
    const base = messageLaneFromMessage({
      content: "帮你梳理一下起步方案：",
      process: [
        { kind: "reasoning", text: "想一下" },
        { kind: "content", text: "帮你梳理一下起步方案：" },
      ],
    });
    const next = foldCheckpointMarker(base, "cp_1");
    expect(next.content).toBe("帮你梳理一下起步方案：");
    expect(next.process).toEqual([
      { kind: "reasoning", text: "想一下" },
      { kind: "content", text: "帮你梳理一下起步方案：" },
      { kind: "checkpoint", checkpoint_id: "cp_1" },
    ]);
  });
});

// Reload 补标记（时间线一期）: journal → positional markers, invariant「有交互卡必有
// 时间线标记」without ever eating settled content (absorb is live-only semantics).
describe("ensureTimelineMarkersFromJournal", () => {
  const journal = [
    { type: "message_start", payload: { message_id: "m1" } },
    { type: "content_delta", payload: { delta: "我来安排团队。" } },
    { type: "run_plan", payload: { execution_id: "exec1" } },
    { type: "checkpoint_required", payload: { checkpoint_id: "cp1" } },
    { type: "plan_review_required", payload: { checkpoint_id: "pr1" } },
  ];

  it("backfills every marker a bare persisted process is missing", () => {
    const process = ensureTimelineMarkersFromJournal(
      [{ kind: "content", text: "我来安排团队。" }],
      journal,
    );
    expect(process).toEqual([
      { kind: "content", text: "我来安排团队。" },
      { kind: "team", execution_id: "exec1" },
      { kind: "checkpoint", checkpoint_id: "cp1" },
      { kind: "plan_review", checkpoint_id: "pr1" },
    ]);
  });

  it("no-ops (dedup) when the persisted process already carries the markers", () => {
    const persisted = [
      { kind: "content", text: "我来安排团队。" },
      { kind: "team_preview", checkpoint_id: "tp1" },
      { kind: "team", execution_id: "exec1" },
      { kind: "checkpoint", checkpoint_id: "cp1" },
      { kind: "plan_review", checkpoint_id: "pr1" },
      { kind: "content", text: "收尾。" },
    ] as const;
    const process = ensureTimelineMarkersFromJournal(
      [...persisted] as Parameters<typeof ensureTimelineMarkersFromJournal>[0],
      journal,
    );
    expect(process).toEqual([...persisted]);
  });

  it("never absorbs settled trailing content", () => {
    const process = ensureTimelineMarkersFromJournal(
      [{ kind: "content", text: "定稿正文，resolve 后的收尾。" }],
      [{ type: "checkpoint_required", payload: { checkpoint_id: "cp9" } }],
    );
    expect(process).toEqual([
      { kind: "content", text: "定稿正文，resolve 后的收尾。" },
      { kind: "checkpoint", checkpoint_id: "cp9" },
    ]);
  });

  it("backfills graph_append and skips team for host_message_id run_plan", () => {
    const process = ensureTimelineMarkersFromJournal(
      [{ kind: "content", text: "再加一人。" }],
      [
        {
          type: "graph_append",
          payload: {
            execution_id: "exec1",
            host_message_id: "m1",
            added_count: 1,
          },
        },
        {
          type: "run_plan",
          payload: {
            execution_id: "exec1",
            host_message_id: "m1",
          },
        },
      ],
    );
    expect(process).toEqual([
      { kind: "content", text: "再加一人。" },
      {
        kind: "graph_append",
        execution_id: "exec1",
        host_message_id: "m1",
        added_count: 1,
      },
    ]);
  });

  // 缺 team 且已有队后 content：按 journal 槽插入，禁止尾部 append 把终稿挤到图上方。
  it("inserts missing team before post-plan content (journal slot)", () => {
    const process = ensureTimelineMarkersFromJournal(
      [
        { kind: "content", text: "我来安排团队。" },
        { kind: "content", text: "终稿已完成。" },
      ],
      [
        { type: "content_delta", payload: { delta: "我来安排团队。" } },
        { type: "run_plan", payload: { execution_id: "exec1" } },
        { type: "content_delta", payload: { delta: "终稿已完成。" } },
      ],
    );
    expect(process).toEqual([
      { kind: "content", text: "我来安排团队。" },
      { kind: "team", execution_id: "exec1" },
      { kind: "content", text: "终稿已完成。" },
    ]);
  });

  // 历史 journal 仍可能带 team_preview marker：插入 team 时保持 marker → 图 → 终稿。
  it("inserts missing team after persisted team_preview (product order)", () => {
    const process = ensureTimelineMarkersFromJournal(
      [
        { kind: "content", text: "我来安排团队。" },
        {
          kind: "team_preview",
          checkpoint_id: "tp1",
        } as unknown as ProcessStep,
        { kind: "content", text: "终稿。" },
      ],
      [
        { type: "content_delta", payload: { delta: "我来安排团队。" } },
        { type: "run_plan", payload: { execution_id: "exec1" } },
        { type: "content_delta", payload: { delta: "终稿。" } },
      ],
    );
    expect(process).toEqual([
      { kind: "content", text: "我来安排团队。" },
      { kind: "team_preview", checkpoint_id: "tp1" },
      { kind: "team", execution_id: "exec1" },
      { kind: "content", text: "终稿。" },
    ]);
  });

  // graph_append 同原则：缺锚点且已有追加后 content → 插在队后 content 之前。
  it("inserts missing graph_append before post-append content (journal slot)", () => {
    const process = ensureTimelineMarkersFromJournal(
      [
        { kind: "content", text: "再加一人。" },
        { kind: "content", text: "已追加。" },
      ],
      [
        { type: "content_delta", payload: { delta: "再加一人。" } },
        {
          type: "graph_append",
          payload: {
            execution_id: "exec1",
            host_message_id: "m1",
            added_count: 1,
          },
        },
        {
          type: "run_plan",
          payload: {
            execution_id: "exec1",
            host_message_id: "m1",
          },
        },
        { type: "content_delta", payload: { delta: "已追加。" } },
      ],
    );
    expect(process).toEqual([
      { kind: "content", text: "再加一人。" },
      {
        kind: "graph_append",
        execution_id: "exec1",
        host_message_id: "m1",
        added_count: 1,
      },
      { kind: "content", text: "已追加。" },
    ]);
  });
  // progressive process_* journals omit content_delta from runs.events — missing team
  // must still pin above all settled content (not legacy append).
  it("pins missing team above process when journal has no pre-plan deltas", () => {
    const process = ensureTimelineMarkersFromJournal(
      [
        { kind: "content", text: "进展。" },
        { kind: "content", text: "终稿。" },
      ],
      [{ type: "run_plan", payload: { execution_id: "exec1" } }],
    );
    expect(process).toEqual([
      { kind: "team", execution_id: "exec1" },
      { kind: "content", text: "进展。" },
      { kind: "content", text: "终稿。" },
    ]);
  });

  it("backfills user_interjection at journal slot and dedupes later statuses", () => {
    const process = ensureTimelineMarkersFromJournal(
      [
        { kind: "content", text: "你好" },
        { kind: "content", text: "，世界！" },
      ],
      [
        { type: "content_delta", payload: { delta: "你好" } },
        {
          type: "user_interjection",
          payload: {
            interjection_id: "inj-1",
            status: "received",
          },
        },
        {
          type: "user_interjection",
          payload: {
            interjection_id: "inj-1",
            status: "injected",
          },
        },
        { type: "content_delta", payload: { delta: "，世界！" } },
      ],
    );
    expect(process).toEqual([
      { kind: "content", text: "你好" },
      { kind: "user_interjection", interjection_id: "inj-1" },
      { kind: "content", text: "，世界！" },
    ]);
  });

  it("no-ops user_interjection when marker already persisted", () => {
    const persisted = [
      { kind: "content", text: "你好" },
      { kind: "user_interjection", interjection_id: "inj-1" },
      { kind: "content", text: "，世界！" },
    ] as const;
    const process = ensureTimelineMarkersFromJournal(
      [...persisted] as Parameters<typeof ensureTimelineMarkersFromJournal>[0],
      [
        {
          type: "user_interjection",
          payload: { interjection_id: "inj-1", status: "received" },
        },
        {
          type: "user_interjection",
          payload: { interjection_id: "inj-1", status: "injected" },
        },
      ],
    );
    expect(process).toEqual([...persisted]);
  });
});

describe("foldTeamMarker", () => {
  it("promotes scalar CEO lead-in before stamping team (图在回复下方)", () => {
    const base = messageLaneFromMessage({
      content: "这是个很有意思的方向",
      process: [{ kind: "reasoning", text: "想一下" }],
    });
    const next = foldTeamMarker(base, "exec1");
    expect(next.process).toEqual([
      { kind: "reasoning", text: "想一下" },
      { kind: "content", text: "这是个很有意思的方向" },
      { kind: "team", execution_id: "exec1" },
    ]);
  });
});
