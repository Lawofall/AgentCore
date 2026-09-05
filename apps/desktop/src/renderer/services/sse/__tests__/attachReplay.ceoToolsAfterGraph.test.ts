// @vitest-environment jsdom
/**
 * 协作图下方重复工具：截图同款事件串走真实 dispatch / foldAttachSegment。
 *
 * 图前是思考隔开的 Consult / List folders / Write file。同一批 `tool_use_start`
 * 再折（增量段不清屏、或全量段 reset 落空）不得在 `team` 后再追加同 id。
 */
import { groupToolRuns } from "@/lib/processTimeline";
import { flushPendingContent } from "@/services/sse/contentBuffer";
import { dispatchSSEEvent } from "@/services/sse/dispatch";
import { foldAttachSegment } from "@/services/streamConversation";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import {
  beginTurnPreflight,
  enterTurnStreaming,
} from "@/stores/conversation/turnPhaseActions";
import { useExecutionStore } from "@/stores/execution";
import type { ProcessStep, SSEEvent } from "@/types/events";
import { beforeEach, describe, expect, it } from "vitest";

const CID = "conv-ceo-tools-after-graph";
const MID = "srv-court-turn";
const EXEC = "exec-court";

function ev(type: string, payload: Record<string, unknown>): SSEEvent {
  return { type, timestamp: "t", payload } as SSEEvent;
}

const RUN_PLAN = ev("run_plan", {
  execution_id: EXEC,
  plan_type: "multi_agent",
  task_summary: "法庭迷局",
  agents: [{ id: "a1", role: "规划系统设计师" }],
  runs: [{ id: "r1", agent_id: "a1", task: "写规则", depends_on: [] }],
});

/** 截图上半场：思考交织的三连工具 + 派队。 */
function liveLeadIn(): SSEEvent[] {
  return [
    ev("message_start", { message_id: MID, conversation_id: CID }),
    ev("reasoning_delta", { delta: "想 1" }),
    ev("tool_use_start", {
      tool_call_id: "c1",
      tool_name: "consult",
      arguments: { name: "team_orchestration_advanced" },
    }),
    ev("tool_use_end", {
      tool_call_id: "c1",
      tool_name: "consult",
      result: "ok",
      status: "success",
    }),
    ev("reasoning_delta", { delta: "想 2" }),
    ev("tool_use_start", {
      tool_call_id: "c2",
      tool_name: "list_folders",
      arguments: {},
    }),
    ev("tool_use_end", {
      tool_call_id: "c2",
      tool_name: "list_folders",
      result: "ok",
      status: "success",
    }),
    ev("reasoning_delta", { delta: "想 3" }),
    ev("tool_use_start", {
      tool_call_id: "c3",
      tool_name: "file_write",
      arguments: { path: "docs/00-创作基准.md" },
    }),
    ev("tool_use_end", {
      tool_call_id: "c3",
      tool_name: "file_write",
      result: "ok",
      status: "success",
    }),
    ev("reasoning_delta", { delta: "开始派队" }),
    RUN_PLAN,
  ];
}

function foldEvents(events: SSEEvent[]): void {
  for (const e of events) {
    dispatchSSEEvent(e, { conversationId: CID, source: "server" });
  }
  flushPendingContent(CID);
}

function processOf(): ProcessStep[] {
  return (
    getRuntime(CID)
      .messages.filter((m) => m.role === "assistant")
      .at(-1)?.process ?? []
  );
}

function toolsAfterTeam(process: ProcessStep[]): string[] {
  const i = process.findIndex((s) => s.kind === "team");
  if (i < 0) return [];
  return process
    .slice(i + 1)
    .filter(
      (s): s is Extract<ProcessStep, { kind: "tool" }> => s.kind === "tool",
    )
    .map((s) => s.tool_name);
}

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useExecutionStore.setState({ byId: {} });
  const conv = useConversationStore.getState();
  conv.switchConversation(CID);
  conv.addMessage({
    id: "u1",
    role: "user",
    content: "设计法庭迷局",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
  beginTurnPreflight(CID);
  enterTurnStreaming(CID);
});

describe("attachReplay · 协作图下 CEO 工具重复", () => {
  it("直播只折一次：图后没有工具，连续三件不会收成 tool-group", () => {
    foldEvents(liveLeadIn());
    const process = processOf();
    expect(toolsAfterTeam(process)).toEqual([]);
    const nodes = groupToolRuns(process);
    const teamAt = nodes.findIndex((n) => n.kind === "team");
    expect(teamAt).toBeGreaterThan(0);
    expect(nodes.slice(teamAt + 1).some((n) => n.kind === "tool-group")).toBe(
      false,
    );
  });

  it("增量段（无 full_replay）再送同一批 start：同 id 不追加，图后没有 tool-group", () => {
    foldEvents(liveLeadIn());
    foldAttachSegment(CID, [
      ev("message_start", { message_id: MID, conversation_id: CID }),
      ev("reasoning_delta", { delta: "想 1" }),
      ev("tool_use_start", {
        tool_call_id: "c1",
        tool_name: "consult",
        arguments: { name: "team_orchestration_advanced" },
      }),
      ev("tool_use_start", {
        tool_call_id: "c2",
        tool_name: "list_folders",
        arguments: {},
      }),
      ev("tool_use_start", {
        tool_call_id: "c3",
        tool_name: "file_write",
        arguments: { path: "docs/00-创作基准.md" },
      }),
      RUN_PLAN,
    ]);
    const process = processOf();
    expect(toolsAfterTeam(process)).toEqual([]);
    const nodes = groupToolRuns(process);
    const afterTeam = nodes.slice(
      nodes.findIndex((n) => n.kind === "team") + 1,
    );
    expect(afterTeam.some((n) => n.kind === "tool-group")).toBe(false);
  });

  it("GET 已水合出图（process 含 team）+ 增量段从思考游标重发 start → 图后仍没有工具", () => {
    useConversationStore.getState().addMessage({
      id: MID,
      role: "assistant",
      content: "",
      createdAt: "",
      executionId: EXEC,
      serverMessageId: MID,
      isStreaming: true,
      status: "running",
      process: [
        { kind: "reasoning", text: "想 1" },
        {
          kind: "tool",
          id: "c1",
          tool_name: "consult",
          arguments: { name: "team_orchestration_advanced" },
          result: "ok",
          status: "success",
        },
        { kind: "reasoning", text: "想 2" },
        {
          kind: "tool",
          id: "c2",
          tool_name: "list_folders",
          arguments: {},
          result: "ok",
          status: "success",
        },
        { kind: "reasoning", text: "想 3" },
        {
          kind: "tool",
          id: "c3",
          tool_name: "file_write",
          arguments: { path: "docs/00-创作基准.md" },
          result: "ok",
          status: "success",
        },
        { kind: "reasoning", text: "开始派队" },
        { kind: "team", execution_id: EXEC },
      ],
    });
    foldAttachSegment(CID, [
      ev("message_start", { message_id: MID, conversation_id: CID }),
      ev("reasoning_delta", { delta: "想 1" }),
      ev("tool_use_start", {
        tool_call_id: "c1",
        tool_name: "consult",
        arguments: { name: "team_orchestration_advanced" },
      }),
      ev("tool_use_start", {
        tool_call_id: "c2",
        tool_name: "list_folders",
        arguments: {},
      }),
      ev("tool_use_start", {
        tool_call_id: "c3",
        tool_name: "file_write",
        arguments: { path: "docs/00-创作基准.md" },
      }),
      RUN_PLAN,
    ]);
    expect(toolsAfterTeam(processOf())).toEqual([]);
  });

  it("full_replay 但用户泡被撤掉：reset 落空，同 id start 仍不叠到图后", () => {
    foldEvents(liveLeadIn());
    useConversationStore.getState().removeMessage("u1", CID);
    foldAttachSegment(CID, [
      ev("message_start", {
        message_id: MID,
        conversation_id: CID,
        full_replay: true,
      }),
      ...liveLeadIn().filter((e) => e.type !== "message_start"),
    ]);
    expect(toolsAfterTeam(processOf())).toEqual([]);
  });

  it("全量段 full_replay 先重置再折：图后仍没有工具", () => {
    foldEvents(liveLeadIn());
    foldAttachSegment(CID, [
      ev("message_start", {
        message_id: MID,
        conversation_id: CID,
        full_replay: true,
      }),
      ...liveLeadIn().filter((e) => e.type !== "message_start"),
    ]);
    expect(toolsAfterTeam(processOf())).toEqual([]);
  });
});
