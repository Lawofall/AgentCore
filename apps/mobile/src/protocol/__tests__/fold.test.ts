// 工具执行阶段进度 (联网搜索前端展示优化): the transport-only live sibling extractToolPhases.
// It reads the running-tool phase off raw SSE events (never journaled, kept OUT of the
// ProjectedTurn) so the mobile waiting UI shows 正在检索 / 排队中 / 改用备用引擎 instead of a
// static 进行中 — and clears a tool's phase the moment it ends.

import {
  extractCoordinationWait,
  extractEscalationSlots,
  extractEvidenceLedger,
  extractExecutionDetached,
  extractGraphAppendActKinds,
  extractGraphAppendAuthorizedBy,
  extractPrevExecutionIds,
  extractStageCardTraces,
  extractToolPhases,
  extractTurnQueued,
  extractWorkerToolPhases,
  fold,
} from "@/protocol/fold";
import type { SSEEvent } from "@agentcore/contract-types";
import { diffProjected, loadFixtures } from "@agentcore/protocol-conformance";
import { describe, expect, it } from "vitest";

function ev(type: SSEEvent["type"], payload: unknown): SSEEvent {
  return { type, timestamp: "", payload } as SSEEvent;
}

describe("extractGraphAppendActKinds", () => {
  it("透传 graph_append.act_kind（开辩论幕）", () => {
    const map = extractGraphAppendActKinds([
      ev("graph_append", {
        execution_id: "exec1",
        host_message_id: "m1",
        append_message_id: "m2",
        added_count: 3,
        act_id: "act-2",
        act_kind: "debate",
      }),
    ]);
    expect(map.get("exec1")).toBe("debate");
  });
});

describe("extractGraphAppendAuthorizedBy / stage_card process", () => {
  it("透传 authorized_by", () => {
    const map = extractGraphAppendAuthorizedBy([
      ev("graph_append", {
        execution_id: "exec1",
        host_message_id: "m1",
        append_message_id: "m2",
        added_count: 1,
        act_kind: "debate",
        authorized_by: "stage_card",
      }),
    ]);
    expect(map.get("exec1")).toBe("stage_card");
  });

  it("stage_card_required 落 process marker；traces 跟踪去向", () => {
    const turn = fold([
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("content_delta", { delta: "调研呈报。" }),
      ev("stage_card_required", {
        stage_card_id: "sc1",
        conversation_id: "c1",
        motion: "是否开辩",
        sides: [],
        form: "debate",
      }),
      ev("message_end", { finish_reason: "end_turn" }),
    ]);
    expect(turn.process.some((s) => s.kind === "stage_card")).toBe(true);
    const traces = extractStageCardTraces([
      ev("stage_card_required", {
        stage_card_id: "sc1",
        conversation_id: "c1",
        motion: "是否开辩",
        sides: [],
        form: "debate",
      }),
      ev("stage_card_resolved", {
        stage_card_id: "sc1",
        decision: "start_debate",
      }),
    ]);
    expect(traces.get("sc1")?.outcome).toBe("resolved");
    expect(traces.get("sc1")?.decision).toBe("start_debate");
  });
});

describe("extractPrevExecutionIds", () => {
  it("透传 run_plan.prev_execution_id", () => {
    const map = extractPrevExecutionIds([
      ev("run_plan", {
        execution_id: "exec2",
        plan_type: "multi_agent",
        task_summary: "续接",
        prev_execution_id: "exec1",
        agents: [],
        runs: [],
      }),
    ]);
    expect(map.get("exec2")).toBe("exec1");
  });
});

describe("fold · graph_append / cross-turn append", () => {
  it("multi_agent_cross_turn_append fixture aligns with golden", () => {
    const fixture = loadFixtures().find(
      (f) => f.name === "multi_agent_cross_turn_append",
    );
    expect(fixture).toBeTruthy();
    if (!fixture) return;
    const actual = fold(fixture.events as SSEEvent[]);
    expect(diffProjected(fixture.projected, actual)).toEqual([]);
  });

  it("旧 journal：graph_append 透传 process 锚点；host_message_id run_plan 不插 team", () => {
    const turn = fold([
      ev("message_start", { message_id: "m2", conversation_id: "c1" }),
      ev("content_delta", { delta: "再加一人。" }),
      ev("graph_append", {
        execution_id: "exec1",
        host_message_id: "m1",
        append_message_id: "m2",
        added_count: 2,
        roles: ["撰写员", "校对"],
        added_run_ids: ["r3", "r4"],
      }),
      ev("run_plan", {
        execution_id: "exec1",
        plan_type: "multi_agent",
        task_summary: "追加",
        host_message_id: "m1",
        agents: [
          {
            id: "w3",
            role: "撰写员",
            thinking: false,
          },
        ],
        runs: [{ id: "r3", agent_id: "w3", task: "写", depends_on: [] }],
      }),
    ]);
    expect(turn.process).toEqual([
      { kind: "content", text: "再加一人。" },
      {
        kind: "graph_append",
        execution_id: "exec1",
        host_message_id: "m1",
        added_count: 2,
      },
    ]);
    expect(turn.process.some((s) => s.kind === "team")).toBe(false);
    expect(turn.runs.map((r) => r.id)).toEqual(["r3"]);
  });

  it("旧 journal：message_start 清正文/process，同 execution_id 保留 agents/runs", () => {
    const turn = fold([
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("content_delta", { delta: "第一回合。" }),
      ev("run_plan", {
        execution_id: "exec1",
        plan_type: "multi_agent",
        task_summary: "建图",
        agents: [
          {
            id: "w1",
            role: "研究员",
            thinking: true,
          },
        ],
        runs: [{ id: "r1", agent_id: "w1", task: "调研", depends_on: [] }],
      }),
      ev("run_started", {
        run_id: "r1",
        agent_id: "w1",
        parent_run_id: null,
        kind: "agent",
      }),
      ev("run_completed", {
        run_id: "r1",
        agent_id: "w1",
        output_summary: "ok",
        duration_ms: 10,
      }),
      ev("message_end", { finish_reason: "end_turn" }),
      ev("message_start", { message_id: "m2", conversation_id: "c1" }),
      ev("content_delta", { delta: "追加回合。" }),
      ev("graph_append", {
        execution_id: "exec1",
        host_message_id: "m1",
        append_message_id: "m2",
        added_count: 1,
      }),
      ev("run_plan", {
        execution_id: "exec1",
        plan_type: "multi_agent",
        task_summary: "生长",
        host_message_id: "m1",
        agents: [
          {
            id: "w1",
            role: "研究员",
            thinking: true,
          },
          {
            id: "w2",
            role: "撰写员",
            thinking: false,
          },
        ],
        runs: [
          { id: "r1", agent_id: "w1", task: "调研", depends_on: [] },
          { id: "r2", agent_id: "w2", task: "写", depends_on: [] },
        ],
      }),
      ev("run_started", {
        run_id: "r2",
        agent_id: "w2",
        parent_run_id: null,
        kind: "agent",
      }),
      ev("run_completed", {
        run_id: "r2",
        agent_id: "w2",
        output_summary: "done",
        duration_ms: 20,
      }),
      ev("message_end", { finish_reason: "end_turn" }),
    ]);
    expect(turn.content).toBe("追加回合。");
    expect(turn.process.map((s) => s.kind)).toEqual([
      "content",
      "graph_append",
    ]);
    expect(turn.agents.map((a) => a.id)).toEqual(["w1", "w2"]);
    expect(turn.runs.map((r) => r.id)).toEqual(["r1", "r2"]);
    expect(turn.runs.find((r) => r.id === "r1")?.status).toBe("completed");
    expect(turn.runs.find((r) => r.id === "r2")?.status).toBe("completed");
    // 不同 execution 才清空重建；同 id merge 不把宿主已完成节点 skip 掉
    expect(turn.runs.every((r) => r.status !== "skipped")).toBe(true);
  });

  it("新契约：prev_execution_id 插本回合 team；换 execution_id 重置图", () => {
    const events = [
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("run_plan", {
        execution_id: "exec1",
        plan_type: "multi_agent",
        task_summary: "建图",
        agents: [{ id: "w1", role: "研究员", thinking: true }],
        runs: [{ id: "r1", agent_id: "w1", task: "调研", depends_on: [] }],
      }),
      ev("run_started", {
        run_id: "r1",
        agent_id: "w1",
        parent_run_id: null,
        kind: "agent",
      }),
      ev("run_completed", {
        run_id: "r1",
        agent_id: "w1",
        output_summary: "ok",
        duration_ms: 10,
      }),
      ev("message_end", { finish_reason: "end_turn" }),
      ev("message_start", { message_id: "m2", conversation_id: "c1" }),
      ev("content_delta", { delta: "续接。" }),
      ev("run_plan", {
        execution_id: "exec2",
        plan_type: "multi_agent",
        task_summary: "新图",
        prev_execution_id: "exec1",
        agents: [{ id: "w3", role: "撰写员", thinking: false }],
        runs: [{ id: "r3", agent_id: "w3", task: "写", depends_on: [] }],
      }),
      ev("run_started", {
        run_id: "r3",
        agent_id: "w3",
        parent_run_id: null,
        kind: "agent",
      }),
      ev("run_completed", {
        run_id: "r3",
        agent_id: "w3",
        output_summary: "done",
        duration_ms: 20,
      }),
      ev("message_end", { finish_reason: "end_turn" }),
    ];
    const turn = fold(events);
    expect(turn.process).toEqual([
      { kind: "content", text: "续接。" },
      { kind: "team", execution_id: "exec2" },
    ]);
    expect(turn.process.some((s) => s.kind === "graph_append")).toBe(false);
    expect(turn.agents.map((a) => a.id)).toEqual(["w3"]);
    expect(turn.runs.map((r) => r.id)).toEqual(["r3"]);
    expect(extractPrevExecutionIds(events).get("exec2")).toBe("exec1");
  });
});

describe("fold · replaces_run_id", () => {
  it("透传 plan.replaces_run_id 与 run_started.replaces_run_id", () => {
    const turn = fold([
      ev("message_start", {
        message_id: "m1",
        conversation_id: "c1",
      }),
      ev("run_plan", {
        execution_id: "e1",
        plan_type: "multi_agent",
        task_summary: "补派",
        agents: [
          { id: "a1", role: "写手", thinking: false },
          {
            id: "a1b",
            role: "写手",
            thinking: false,
          },
        ],
        runs: [
          { id: "r1", agent_id: "a1", task: "写", depends_on: [] },
          {
            id: "r1b",
            agent_id: "a1b",
            task: "写（补派）",
            depends_on: [],
            replaces_run_id: "r1",
          },
        ],
      }),
      ev("run_started", {
        run_id: "r1b",
        agent_id: "a1b",
        parent_run_id: null,
        kind: "agent",
        replaces_run_id: "r1",
      }),
    ]);
    expect(turn.runs.find((r) => r.id === "r1b")?.replacesRunId).toBe("r1");
  });
});

describe("fold · tool_use_end.failure", () => {
  it("folds failure onto the matching tool step when status=error", () => {
    const turn = fold([
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("tool_use_start", {
        tool_call_id: "tc1",
        tool_name: "web_search",
        arguments: { query: "x" },
      }),
      ev("tool_use_end", {
        tool_call_id: "tc1",
        tool_name: "web_search",
        status: "error",
        result: "ConnectError: refused searxng.internal:8080",
        failure: {
          message: "工具执行失败，请稍后重试。",
          code: "TOOL_ERROR",
        },
      }),
      ev("message_end", { finish_reason: "end_turn" }),
    ]);
    const tool = turn.process.find((s) => s.kind === "tool");
    expect(tool).toMatchObject({
      kind: "tool",
      id: "tc1",
      status: "error",
      result: "ConnectError: refused searxng.internal:8080",
      failure: {
        message: "工具执行失败，请稍后重试。",
        code: "TOOL_ERROR",
      },
    });
  });

  it("leaves failure absent when tool_use_end omits it", () => {
    const turn = fold([
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("tool_use_start", {
        tool_call_id: "tc1",
        tool_name: "web_search",
        arguments: { query: "x" },
      }),
      ev("tool_use_end", {
        tool_call_id: "tc1",
        tool_name: "web_search",
        status: "error",
        result: "legacy technical text",
      }),
      ev("message_end", { finish_reason: "end_turn" }),
    ]);
    const tool = turn.process.find((s) => s.kind === "tool");
    expect(tool?.kind).toBe("tool");
    if (tool?.kind === "tool") {
      expect(tool.failure).toBeUndefined();
      expect(tool.result).toBe("legacy technical text");
    }
  });
});

describe("extractToolPhases", () => {
  it("keeps the LATEST phase per running tool_call_id", () => {
    const phases = extractToolPhases([
      ev("tool_use_start", { tool_call_id: "c1", tool_name: "web_search" }),
      ev("tool_use_progress", {
        tool_call_id: "c1",
        tool_name: "web_search",
        phase: "querying",
      }),
      ev("tool_use_progress", {
        tool_call_id: "c1",
        tool_name: "web_search",
        phase: "fallback",
      }),
    ]);
    expect(phases.get("c1")).toBe("fallback");
  });

  it("clears a tool's phase on its matching tool_use_end", () => {
    const phases = extractToolPhases([
      ev("tool_use_progress", {
        tool_call_id: "c1",
        tool_name: "web_search",
        phase: "querying",
      }),
      ev("tool_use_end", {
        tool_call_id: "c1",
        tool_name: "web_search",
        result: "ok",
        status: "success",
      }),
    ]);
    expect(phases.has("c1")).toBe(false);
  });

  it("tracks concurrent tool calls independently", () => {
    const phases = extractToolPhases([
      ev("tool_use_progress", {
        tool_call_id: "c1",
        tool_name: "web_search",
        phase: "queued",
      }),
      ev("tool_use_progress", {
        tool_call_id: "c2",
        tool_name: "web_search",
        phase: "querying",
      }),
      ev("tool_use_end", {
        tool_call_id: "c1",
        tool_name: "web_search",
        result: "ok",
        status: "success",
      }),
    ]);
    expect(phases.get("c1")).toBeUndefined();
    expect(phases.get("c2")).toBe("querying");
  });

  it("returns an empty map for a turn with no progress events (history replay)", () => {
    const phases = extractToolPhases([
      ev("content_delta", { delta: "hi" }),
      ev("tool_use_start", { tool_call_id: "c1", tool_name: "web_search" }),
      ev("tool_use_end", {
        tool_call_id: "c1",
        tool_name: "web_search",
        result: "ok",
        status: "success",
      }),
    ]);
    expect(phases.size).toBe(0);
  });
});

describe("extractCoordinationWait", () => {
  it("keeps the latest waiting n/m", () => {
    expect(
      extractCoordinationWait([
        ev("coordination_wait", {
          execution_id: "e1",
          waiting: true,
          completed: 0,
          total: 2,
        }),
        ev("coordination_wait", {
          execution_id: "e1",
          waiting: true,
          completed: 1,
          total: 2,
        }),
      ]),
    ).toEqual({ completed: 1, total: 2 });
  });

  it("clears when waiting=false", () => {
    expect(
      extractCoordinationWait([
        ev("coordination_wait", {
          execution_id: "e1",
          waiting: true,
          completed: 1,
          total: 2,
        }),
        ev("coordination_wait", {
          execution_id: "e1",
          waiting: false,
          completed: 2,
          total: 2,
        }),
      ]),
    ).toBeNull();
  });
});

describe("extractExecutionDetached", () => {
  it("stays true after execution_detached", () => {
    expect(
      extractExecutionDetached([
        ev("execution_detached", {
          execution_id: "e1",
          conversation_id: "c1",
          completed: 1,
          total: 2,
        }),
      ]),
    ).toBe(true);
  });

  it("clears on execution_completed", () => {
    expect(
      extractExecutionDetached([
        ev("execution_detached", {
          execution_id: "e1",
          conversation_id: "c1",
          completed: 1,
          total: 2,
        }),
        ev("execution_completed", {
          execution_id: "e1",
          conversation_id: "c1",
          completed: 2,
          total: 2,
          status: "completed",
        }),
      ]),
    ).toBe(false);
  });
});

describe("extractWorkerToolPhases", () => {
  it("keeps the LATEST phase per worker run_id", () => {
    const phases = extractWorkerToolPhases([
      ev("tool_use_progress", {
        tool_call_id: "c1",
        tool_name: "web_search",
        phase: "queued",
        run_id: "run-2",
      }),
      ev("tool_use_progress", {
        tool_call_id: "c1",
        tool_name: "web_search",
        phase: "querying",
        run_id: "run-2",
      }),
    ]);
    expect(phases.get("run-2")).toEqual({
      phase: "querying",
      toolName: "web_search",
    });
  });

  it("ignores CEO-scoped progress (no run_id)", () => {
    const phases = extractWorkerToolPhases([
      ev("tool_use_progress", {
        tool_call_id: "c1",
        tool_name: "web_search",
        phase: "querying",
      }),
    ]);
    expect(phases.size).toBe(0);
  });

  it("clears a worker phase on tool_use_end with run_id", () => {
    const phases = extractWorkerToolPhases([
      ev("tool_use_progress", {
        tool_call_id: "c1",
        tool_name: "web_search",
        phase: "fallback",
        run_id: "run-9",
      }),
      ev("tool_use_end", {
        tool_call_id: "c1",
        tool_name: "web_search",
        result: "ok",
        status: "success",
        run_id: "run-9",
      }),
    ]);
    expect(phases.size).toBe(0);
  });
});

describe("extractEvidenceLedger", () => {
  it("合并 debate_pretrial_completed.evidence_ledger_delta（#e1 不靠收场后再补）", () => {
    const ledger = extractEvidenceLedger([
      ev("debate_pretrial_completed", {
        execution_id: "exec1",
        moderator_run_id: "mod",
        status: "done",
        evidence_ledger_delta: [
          {
            id: "#e1",
            title: "庭前证据",
            url: "https://example.com/e1",
            site: "example.com",
          },
        ],
      }),
    ]);
    expect(ledger.map((e) => e.id)).toEqual(["#e1"]);
  });

  it("pretrial delta 与 round delta 累积；debate_result 权威覆盖", () => {
    const ledger = extractEvidenceLedger([
      ev("debate_pretrial_completed", {
        execution_id: "exec1",
        moderator_run_id: "mod",
        evidence_ledger_delta: [
          { id: "#e1", title: "pretrial", url: "https://a.example" },
        ],
      }),
      ev("debate_round", {
        execution_id: "exec1",
        moderator_run_id: "mod",
        round_no: 1,
        focus: "焦点",
        summary: "",
        verdict: null,
        sides: [],
        clashes: [],
        evidence_ledger_delta: [
          { id: "#e2", title: "round", url: "https://b.example" },
        ],
      }),
      ev("debate_result", {
        execution_id: "exec1",
        evidence_ledger: [
          { id: "#e9", title: "final", url: "https://z.example" },
        ],
      }),
    ]);
    expect(ledger.map((e) => e.id)).toEqual(["#e9"]);
  });
});

describe("fold · run_phase", () => {
  it("multi_agent_run_phase fixture aligns with golden", () => {
    const fixture = loadFixtures().find(
      (f) => f.name === "multi_agent_run_phase",
    );
    expect(fixture).toBeTruthy();
    if (!fixture) return;
    const actual = fold(fixture.events as SSEEvent[]);
    expect(diffProjected(fixture.projected, actual)).toEqual([]);
  });

  it("winding_down sticky; tool sets phaseTool; terminal clears phase", () => {
    const base = [
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("run_plan", {
        execution_id: "exec1",
        plan_type: "multi_agent",
        task_summary: "t",
        agents: [{ id: "w1", role: "写手", thinking: true }],
        runs: [{ id: "r1", agent_id: "w1", task: "改", depends_on: [] }],
      }),
      ev("run_started", {
        run_id: "r1",
        agent_id: "w1",
        parent_run_id: null,
        kind: "agent",
      }),
    ];
    let turn = fold([
      ...base,
      ev("run_phase", {
        run_id: "r1",
        agent_id: "w1",
        phase: "tool",
        tool_name: "file_read",
      }),
    ]);
    expect(turn.runs[0]?.phase).toBe("tool");
    expect(turn.runs[0]?.phaseTool).toBe("file_read");

    turn = fold([
      ...base,
      ev("run_phase", { run_id: "r1", agent_id: "w1", phase: "winding_down" }),
      ev("run_phase", {
        run_id: "r1",
        agent_id: "w1",
        phase: "thinking",
      }),
      ev("run_phase", {
        run_id: "r1",
        agent_id: "w1",
        phase: "tool",
        tool_name: "handoff",
      }),
    ]);
    expect(turn.runs[0]?.phase).toBe("winding_down");
    expect(turn.runs[0]?.phaseTool).toBeNull();

    turn = fold([
      ...base,
      ev("run_phase", {
        run_id: "r1",
        agent_id: "w1",
        phase: "waiting_children",
      }),
      ev("run_completed", {
        run_id: "r1",
        agent_id: "w1",
        output_summary: "done",
        duration_ms: 1,
      }),
    ]);
    expect(turn.runs[0]?.phase).toBeUndefined();
    expect(turn.runs[0]?.phaseTool).toBeUndefined();
  });
});

describe("extractTurnQueued", () => {
  it("读取 position / queue_id / degraded_from", () => {
    expect(
      extractTurnQueued([
        ev("turn_queued", {
          queue_id: "q1",
          position: 2,
          queue_depth: 3,
          conversation_id: "c1",
          degraded_from: "steer",
        }),
      ]),
    ).toEqual([
      {
        position: 2,
        queueDepth: 3,
        queueId: "q1",
        degradedFrom: "steer",
      },
    ]);
  });

  it("多项 FIFO 并存（勿单槽覆盖）", () => {
    expect(
      extractTurnQueued([
        ev("turn_queued", {
          queue_id: "q1",
          position: 1,
          queue_depth: 2,
          conversation_id: "c1",
        }),
        ev("turn_queued", {
          queue_id: "q2",
          position: 2,
          queue_depth: 2,
          conversation_id: "c1",
        }),
      ]),
    ).toEqual([
      {
        position: 1,
        queueDepth: 2,
        queueId: "q1",
        degradedFrom: undefined,
      },
      {
        position: 2,
        queueDepth: 2,
        queueId: "q2",
        degradedFrom: undefined,
      },
    ]);
  });

  it("turn_queue_cancelled 按 queue_id 清一项（保留其它）", () => {
    expect(
      extractTurnQueued([
        ev("turn_queued", {
          queue_id: "q1",
          position: 1,
          queue_depth: 2,
          conversation_id: "c1",
        }),
        ev("turn_queued", {
          queue_id: "q2",
          position: 2,
          queue_depth: 2,
          conversation_id: "c1",
        }),
        ev("turn_queue_cancelled", {
          queue_id: "q1",
          conversation_id: "c1",
        }),
      ]),
    ).toEqual([
      {
        position: 2,
        queueDepth: 2,
        queueId: "q2",
        degradedFrom: undefined,
      },
    ]);
  });

  it("turn_queue_cancelled 清唯一项 → 空列表", () => {
    expect(
      extractTurnQueued([
        ev("turn_queued", {
          queue_id: "q1",
          position: 1,
          queue_depth: 1,
          conversation_id: "c1",
        }),
        ev("turn_queue_cancelled", {
          queue_id: "q1",
          conversation_id: "c1",
        }),
      ]),
    ).toEqual([]);
  });

  it("turn_queue_started 按 queue_id 清一项（出队开跑；保留其它）", () => {
    expect(
      extractTurnQueued([
        ev("turn_queued", {
          queue_id: "q1",
          position: 1,
          queue_depth: 2,
          conversation_id: "c1",
        }),
        ev("turn_queued", {
          queue_id: "q2",
          position: 2,
          queue_depth: 2,
          conversation_id: "c1",
        }),
        ev("turn_queue_started", {
          queue_id: "q1",
          conversation_id: "c1",
          remaining_depth: 1,
        }),
      ]),
    ).toEqual([
      {
        position: 2,
        queueDepth: 2,
        queueId: "q2",
        degradedFrom: undefined,
      },
    ]);
  });

  it("message_start 不猜出队（否决启发式）", () => {
    expect(
      extractTurnQueued([
        ev("turn_queued", {
          queue_id: "q1",
          position: 1,
          queue_depth: 1,
          conversation_id: "c1",
        }),
        ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ]),
    ).toEqual([
      {
        position: 1,
        queueDepth: 1,
        queueId: "q1",
        degradedFrom: undefined,
      },
    ]);
  });

  it("fold 对 turn_queue_started no-op（不炸 assertNever）", () => {
    const turn = fold([
      ev("turn_queue_started", {
        queue_id: "q1",
        conversation_id: "c1",
        remaining_depth: 0,
        // live 入场不在 fold：帧正文由 ChatPage 插泡，fold 仍忽略 content。
        content: "queued text stays out of fold",
      }),
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("content_delta", { delta: "ok" }),
      ev("message_end", { finish_reason: "end_turn" }),
    ]);
    expect(turn.content).toBe("ok");
    expect(turn.status).toBe("completed");
  });

  it("fold 对 turn_queue_cancelled no-op（不炸 assertNever）", () => {
    const turn = fold([
      ev("turn_queued", {
        queue_id: "q1",
        position: 1,
        queue_depth: 1,
        conversation_id: "c1",
      }),
      ev("turn_queue_cancelled", {
        queue_id: "q1",
        conversation_id: "c1",
      }),
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("content_delta", { delta: "ok" }),
      ev("message_end", { finish_reason: "end_turn" }),
    ]);
    expect(turn.content).toBe("ok");
    expect(turn.status).toBe("completed");
  });

  it("经典插话 received→injected 同 id 保最新", () => {
    const turn = fold([
      ev("user_interjection", {
        interjection_id: "inj-1",
        execution_id: "exec-1",
        content: "改成中文",
        status: "received",
      }),
      ev("user_interjection", {
        interjection_id: "inj-1",
        execution_id: "exec-1",
        content: "改成中文",
        status: "injected",
      }),
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("content_delta", { delta: "好的" }),
      ev("message_end", { finish_reason: "end_turn" }),
    ]);
    expect(turn.content).toBe("好的");
    expect(turn.userInterjections).toEqual([
      {
        interjectionId: "inj-1",
        executionId: "exec-1",
        content: "改成中文",
        status: "injected",
        note: null,
      },
    ]);
  });

  it("经典降级 received→queued 再被终态覆盖；turn_queued fold no-op", () => {
    const turn = fold([
      ev("user_interjection", {
        interjection_id: "inj-q",
        execution_id: "exec-1",
        content: "晚到",
        status: "received",
      }),
      ev("user_interjection", {
        interjection_id: "inj-q",
        execution_id: "exec-1",
        content: "晚到",
        status: "queued",
        note: "当前回合已收口，已自动转入下一回合",
      }),
      ev("turn_queued", {
        queue_id: "q1",
        position: 1,
        queue_depth: 1,
        conversation_id: "c1",
        degraded_from: "steer",
      }),
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("content_delta", { delta: "ok" }),
      ev("message_end", { finish_reason: "end_turn" }),
    ]);
    expect(turn.userInterjections).toEqual([
      {
        interjectionId: "inj-q",
        executionId: "exec-1",
        content: "晚到",
        status: "queued",
        note: "当前回合已收口，已自动转入下一回合",
      },
    ]);
  });

  it("插话 marker 钉在 received 当时的 process 末尾；同 id 不重复", () => {
    const turn = fold([
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("content_delta", { delta: "你好" }),
      ev("user_interjection", {
        interjection_id: "inj-mid",
        execution_id: "exec-1",
        content: "改成中文",
        status: "received",
      }),
      ev("user_interjection", {
        interjection_id: "inj-mid",
        execution_id: "exec-1",
        content: "改成中文",
        status: "injected",
      }),
      ev("content_delta", { delta: "，世界" }),
      ev("message_end", { finish_reason: "end_turn" }),
    ]);
    expect(turn.process).toEqual([
      { kind: "content", text: "你好" },
      { kind: "user_interjection", interjection_id: "inj-mid" },
      { kind: "content", text: "，世界" },
    ]);
  });

  it("fold 透传 user_interjection.agent_mentions → agentMentions", () => {
    const turn = fold([
      ev("user_interjection", {
        interjection_id: "inj-m",
        execution_id: "exec-1",
        content: "请让研究员再核一遍成本。",
        status: "received",
        agent_mentions: [{ agent_id: "agent_research", role: "研究员" }],
      }),
      ev("user_interjection", {
        interjection_id: "inj-m",
        execution_id: "exec-1",
        content: "请让研究员再核一遍成本。",
        status: "injected",
        agent_mentions: [{ agent_id: "agent_research", role: "研究员" }],
      }),
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("content_delta", { delta: "好的" }),
      ev("message_end", { finish_reason: "end_turn" }),
    ]);
    expect(turn.userInterjections).toEqual([
      {
        interjectionId: "inj-m",
        executionId: "exec-1",
        content: "请让研究员再核一遍成本。",
        status: "injected",
        note: null,
        agentMentions: [{ agentId: "agent_research", role: "研究员" }],
      },
    ]);
  });
});

// attach 增量重放的帧级替换：增量段里「还没说完的那一步」带整步文字、未被 process 行覆盖的
// 通道带整路快照，都以 payload.replace=true 标出。语义是「换掉该通道末尾那个尚未闭合的块」
// ——不是替换整路（会抹掉前面已闭合的步骤），也不是追加（会重复）。直播帧永不带。
describe("fold · replace（attach 增量重放的整块替换帧）", () => {
  const ceoReplay: SSEEvent[] = [
    ev("message_start", { message_id: "m1", conversation_id: "c1" }),
    ev("content_delta", { delta: "第一步已经交代完了。" }),
    ev("reasoning_delta", { delta: "先想清楚再往下写" }),
    ev("content_delta", { delta: "第二步开" }),
    ev("content_delta", {
      delta: "第二步开了个头，还没说完。",
      replace: true,
    }),
  ];

  it("只换末尾开放块：已闭合的步骤原样不动", () => {
    const turn = fold(ceoReplay);
    expect(turn.process).toEqual([
      { kind: "content", text: "第一步已经交代完了。" },
      { kind: "reasoning", text: "先想清楚再往下写" },
      { kind: "content", text: "第二步开了个头，还没说完。" },
    ]);
    expect(turn.content).toBe("第一步已经交代完了。第二步开了个头，还没说完。");
    expect(turn.reasoning).toBe("先想清楚再往下写");
  });

  it("同一事件数组重复 fold 结果一致（fold 纯函数，不改事件）", () => {
    expect(fold(ceoReplay)).toEqual(fold(ceoReplay));
  });

  it("连着两帧 replace 不累积：以最后一帧为准", () => {
    const turn = fold([
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("content_delta", { delta: "开头" }),
      ev("content_delta", { delta: "开头写到一半", replace: true }),
      ev("content_delta", { delta: "开头写到一半又续了两句", replace: true }),
    ]);
    expect(turn.process).toEqual([
      { kind: "content", text: "开头写到一半又续了两句" },
    ]);
    expect(turn.content).toBe("开头写到一半又续了两句");
  });

  it("末尾不是同类步（整路快照先到）：本帧全文自成新块", () => {
    const turn = fold([
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("reasoning_delta", { delta: "在想" }),
      ev("content_delta", { delta: "这一路的整段正文。", replace: true }),
    ]);
    expect(turn.process).toEqual([
      { kind: "reasoning", text: "在想" },
      { kind: "content", text: "这一路的整段正文。" },
    ]);
    expect(turn.content).toBe("这一路的整段正文。");
    expect(turn.reasoning).toBe("在想");
  });

  it("思考通道同理：只换末尾那个未闭合的思考块", () => {
    const turn = fold([
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("reasoning_delta", { delta: "第一段思考。" }),
      ev("content_delta", { delta: "先说一句。" }),
      ev("reasoning_delta", { delta: "第二段想" }),
      ev("reasoning_delta", { delta: "第二段想到这里。", replace: true }),
    ]);
    expect(turn.process).toEqual([
      { kind: "reasoning", text: "第一段思考。" },
      { kind: "content", text: "先说一句。" },
      { kind: "reasoning", text: "第二段想到这里。" },
    ]);
    expect(turn.reasoning).toBe("第一段思考。第二段想到这里。");
    expect(turn.content).toBe("先说一句。");
  });

  const workerReplay: SSEEvent[] = [
    ev("message_start", { message_id: "m1", conversation_id: "c1" }),
    ev("run_plan", {
      execution_id: "exec1",
      plan_type: "multi_agent",
      task_summary: "调研",
      agents: [{ id: "w1", role: "调研员", thinking: true }],
      runs: [{ id: "r1", agent_id: "w1", task: "调研", depends_on: [] }],
    }),
    ev("run_started", {
      run_id: "r1",
      agent_id: "w1",
      parent_run_id: null,
      kind: "agent",
    }),
    ev("run_output_delta", {
      run_id: "r1",
      agent_id: "w1",
      delta: "先给结论：",
    }),
    ev("run_reasoning_delta", {
      run_id: "r1",
      agent_id: "w1",
      delta: "核一遍",
    }),
    ev("run_output_delta", { run_id: "r1", agent_id: "w1", delta: "细节一" }),
    ev("run_output_delta", {
      run_id: "r1",
      agent_id: "w1",
      delta: "细节一、细节二，还没写完。",
      replace: true,
    }),
  ];

  it("队员输出同理：只换末尾开放块，队员卡输出标量同步截换", () => {
    const turn = fold(workerReplay);
    const run = turn.runs.find((r) => r.id === "r1");
    expect(run?.process).toEqual([
      { kind: "content", text: "先给结论：" },
      { kind: "reasoning", text: "核一遍" },
      { kind: "content", text: "细节一、细节二，还没写完。" },
    ]);
    const agent = turn.agents.find((a) => a.id === "w1");
    expect(agent?.output).toBe("先给结论：细节一、细节二，还没写完。");
    expect(agent?.reasoning).toBe("核一遍");
  });

  it("队员侧同一事件数组重复 fold 结果一致", () => {
    expect(fold(workerReplay)).toEqual(fold(workerReplay));
  });
});

describe("extractEscalationSlots · browser_login transport", () => {
  it("maps wire browser_login → esc.browserLogin (transport-only)", () => {
    const slots = extractEscalationSlots([
      ev("escalation_required", {
        escalation_id: "esc-login",
        run_id: "r1",
        agent_id: "w1",
        question: "请在浏览器里登录后再继续",
        assumption: "用户已登录",
        browser_login: true,
      }),
    ]);
    const slot = slots.get("esc-login");
    expect(slot?.esc).toMatchObject({
      status: "pending",
      blocking: true,
      browserLogin: true,
      question: "请在浏览器里登录后再继续",
    });
  });

  it("omits browserLogin when wire flag absent / false", () => {
    const slots = extractEscalationSlots([
      ev("escalation_required", {
        escalation_id: "esc-plain",
        run_id: "r1",
        agent_id: "w1",
        question: "要换方案吗？",
        assumption: "保持原方案",
      }),
    ]);
    expect(slots.get("esc-plain")?.esc.browserLogin).toBeUndefined();
  });

  it("does not fold browserLogin onto ProjectedRun.escalations (golden-clean)", () => {
    const turn = fold([
      ev("message_start", { message_id: "m1", conversation_id: "c1" }),
      ev("run_plan", {
        execution_id: "exec1",
        plan_type: "multi_agent",
        task_summary: "t",
        agents: [{ id: "w1", role: "调研员", thinking: false }],
        runs: [{ id: "r1", agent_id: "w1", task: "调研", depends_on: [] }],
      }),
      ev("run_started", {
        run_id: "r1",
        agent_id: "w1",
        parent_run_id: null,
        kind: "agent",
      }),
      ev("escalation_required", {
        escalation_id: "esc-login",
        run_id: "r1",
        agent_id: "w1",
        question: "请登录",
        assumption: "已登录",
        browser_login: true,
      }),
    ]);
    const esc = turn.runs.find((r) => r.id === "r1")?.escalations[0];
    expect(esc).toMatchObject({
      status: "pending",
      question: "请登录",
    });
    expect(
      (esc as { browserLogin?: boolean } | undefined)?.browserLogin,
    ).toBeUndefined();
  });
});

describe("fold · CEO rate-limit pause", () => {
  it("multi_agent_ceo_rate_limit_paused pins outcome=paused without a gate", () => {
    const fixture = loadFixtures().find(
      (f) => f.name === "multi_agent_ceo_rate_limit_paused",
    );
    expect(fixture).toBeTruthy();
    if (!fixture) return;
    const actual = fold(fixture.events as SSEEvent[]);
    expect(diffProjected(fixture.projected, actual)).toEqual([]);
    expect(actual.outcome).toBe("paused");
    expect(actual.status).toBe("paused");
    expect(actual.finishReason).toBe("paused");
    expect(actual.interactions).toEqual([]);
    expect(actual.runs[0]?.productLanded).toBe(true);
  });
});
