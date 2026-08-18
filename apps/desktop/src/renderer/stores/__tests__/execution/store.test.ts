import { beforeEach, describe, expect, it } from "vitest";
import {
  type ExecutionJournal,
  type ExecutionPlan,
  type RunFrame,
  elapsedMs,
  execRuntime,
  projectExecution,
  projectRuntime,
  reasoningMeta,
} from "../../execution";
import { MID, plan, resetExecutionStore, rt, started, store } from "./fixtures";

beforeEach(() => {
  resetExecutionStore();
});

describe("elapsedMs (task duration)", () => {
  it("is 0 for an empty or single-frame stream", () => {
    expect(elapsedMs([])).toBe(0);
    expect(elapsedMs([started("agent-1", "run-1", 5000)])).toBe(0);
  });

  it("is the wall-clock span between first and last frame", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1", 1000),
      { t: 2500, kind: "run_progress", completed: 1, total: 3 },
      {
        t: 155000,
        kind: "run_completed",
        runId: "run-3",
        agentId: "agent-1",
        outputSummary: "done",
        durationMs: 1,
      },
    ];
    expect(elapsedMs(frames)).toBe(154000);
  });
});

describe("execution store", () => {
  it("startExecution seeds the plan and resets the stream", () => {
    store().recordFrame(
      { t: 0, kind: "run_progress", completed: 1, total: 2 },
      MID,
    );
    store().startExecution(plan, MID);
    expect(rt().plan?.id).toBe("exec-1");
    expect(rt().frames).toEqual([]);
    expect(rt().playhead).toBeNull();
    expect(rt().status).toBe("running");
  });

  it("recordFrame is a no-op without an active plan", () => {
    store().recordFrame(
      { t: 1, kind: "run_progress", completed: 1, total: 2 },
      MID,
    );
    expect(rt().frames).toEqual([]);
  });

  it("recordFrame appends once a plan exists", () => {
    store().startExecution(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    expect(rt().frames).toHaveLength(1);
  });

  it("setPlayhead / goLive move the scrubber", () => {
    store().startExecution(plan, MID);
    store().setPlayhead(0, MID);
    expect(rt().playhead).toBe(0);
    store().goLive(MID);
    expect(rt().playhead).toBeNull();
  });

  it("clearExecution wipes plan, frames and playhead", () => {
    store().startExecution(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    store().clearExecution(MID);
    expect(rt().plan).toBeNull();
    expect(rt().frames).toEqual([]);
    expect(rt().playhead).toBeNull();
    expect(rt().status).toBe("planning");
  });

  it("keeps each message's execution isolated (§9.3)", () => {
    // Two turns stream concurrently into their own slots; neither sees the other.
    store().startExecution(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    store().startExecution({ ...plan, id: "exec-2" }, "msg-2");
    expect(execRuntime(store(), MID).plan?.id).toBe("exec-1");
    expect(execRuntime(store(), MID).frames).toHaveLength(1);
    expect(execRuntime(store(), "msg-2").plan?.id).toBe("exec-2");
    expect(execRuntime(store(), "msg-2").frames).toEqual([]);
    // Clearing one leaves the other intact.
    store().clearExecution("msg-2");
    expect(execRuntime(store(), MID).plan?.id).toBe("exec-1");
  });

  it("alignTurnKey moves plan from client bubble id to server turn id", () => {
    store().startExecution(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    store().alignTurnKey(MID, "msg-server");
    expect(execRuntime(store(), MID).plan).toBeNull();
    expect(execRuntime(store(), "msg-server").plan?.id).toBe("exec-1");
    expect(execRuntime(store(), "msg-server").frames).toHaveLength(1);
  });
});

describe("hydrateFromJournal (reload replay, §9.3)", () => {
  // A persisted turn's journal: the raw run/tool SSE events (run_plan folds into
  // the plan; the rest become frames) plus the turn's finish_reason.
  const journal: ExecutionJournal = {
    finishReason: "stop",
    events: [
      {
        type: "run_plan",
        timestamp: "2026-01-01T00:00:00.000Z",
        payload: {
          execution_id: "exec-1",
          plan_type: "multi_agent",
          task_summary: "分析对比 React 和 Vue",
          agents: [{ id: "agent-1", role: "React 研究员" }],
          runs: [
            {
              id: "run-1",
              agent_id: "agent-1",
              task: "研究 React",
              depends_on: [],
            },
          ],
        },
      },
      {
        type: "run_started",
        timestamp: "2026-01-01T00:00:01.000Z",
        payload: {
          agent_id: "agent-1",
          run_id: "run-1",
          parent_run_id: null,
          kind: "agent",
        },
      },
      {
        type: "run_completed",
        timestamp: "2026-01-01T00:00:02.000Z",
        payload: {
          run_id: "run-1",
          agent_id: "agent-1",
          output_summary: "done",
          duration_ms: 1000,
        },
      },
    ],
  };

  it("rebuilds the plan + frame stream from a persisted journal", () => {
    store().hydrateFromJournal(MID, journal);
    const r = rt();
    expect(r.plan?.id).toBe("exec-1");
    expect(r.plan?.runs).toHaveLength(1);
    // run_plan folds into the plan; the two run frames make the stream.
    expect(r.frames).toHaveLength(2);
    expect(r.status).toBe("completed");
    // Replays through the same fold as the live stream.
    const p = r.plan;
    if (p) {
      const exec = projectExecution(p, r.frames, r.status);
      expect(exec.runs.find((s) => s.id === "run-1")?.status).toBe("completed");
    }
  });

  it("reconstructs a worker's full output + thinking from synthesized deltas (deltas 退场)", () => {
    // The backend no longer journals per-token run_output_delta / run_reasoning_delta.
    // runs_from_entries synthesizes ONE of each per worker (from its message_final),
    // reasoning before content, spliced just before run_completed — this is the exact
    // event shape a reload now receives. The UNCHANGED fold must rebuild 思考全文 + 输出
    // from it (the cross-layer 后端投影 ↔ 桌面 fold alignment that replaces the live
    // delta stream on reload).
    const synthesized: ExecutionJournal = {
      finishReason: "stop",
      events: [
        {
          type: "run_plan",
          timestamp: "2026-01-01T00:00:00.000Z",
          payload: {
            execution_id: "exec-1",
            plan_type: "multi_agent",
            task_summary: "T",
            agents: [{ id: "agent-1", role: "研究员" }],
            runs: [
              {
                id: "run-1",
                agent_id: "agent-1",
                task: "研究",
                depends_on: [],
              },
            ],
          },
        },
        {
          type: "run_started",
          timestamp: "2026-01-01T00:00:01.000Z",
          payload: {
            agent_id: "agent-1",
            run_id: "run-1",
            parent_run_id: null,
            kind: "agent",
          },
        },
        {
          type: "run_reasoning_delta",
          timestamp: "2026-01-01T00:00:02.000Z",
          payload: { run_id: "run-1", agent_id: "agent-1", delta: "完整思考" },
        },
        {
          type: "run_output_delta",
          timestamp: "2026-01-01T00:00:02.000Z",
          payload: { run_id: "run-1", agent_id: "agent-1", delta: "完整输出" },
        },
        {
          type: "run_completed",
          timestamp: "2026-01-01T00:00:02.000Z",
          payload: {
            run_id: "run-1",
            agent_id: "agent-1",
            output_summary: "摘要",
            duration_ms: 1000,
          },
        },
      ],
    };
    store().hydrateFromJournal(MID, synthesized);
    const r = rt();
    const p = r.plan;
    expect(p).toBeTruthy();
    if (!p) return;
    const exec = projectExecution(p, r.frames, r.status);
    const agent = exec.agents.find((a) => a.id === "agent-1");
    // Full output + thinking are reconstructed from the synthesized single-block deltas.
    expect(agent?.outputChunks.join("")).toBe("完整输出");
    expect(agent?.reasoningChunks.join("")).toBe("完整思考");
    const run = exec.runs.find((s) => s.id === "run-1");
    expect(run?.status).toBe("completed");
    expect(run?.outputSummary).toBe("摘要");
  });

  it("does not roll back a live slot when journal is not newer", () => {
    // Live already has the full 3-run plan + a live frame; journal is a smaller
    // snapshot (1 run). Hydrate must not clobber — protection for SSE-ahead state.
    store().startExecution(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    store().hydrateFromJournal(MID, journal);
    expect(rt().plan?.id).toBe("exec-1");
    expect(rt().plan?.runs).toHaveLength(3);
    expect(rt().frames).toHaveLength(1);
  });

  it("hydrates when journal settled a worker live still shows running (terminal lead)", () => {
    // Detach sample: live accumulated many ephemeral deltas; journal is sparse
    // but already has run_completed. frames.length alone would refuse — terminal
    // lead must catch up so the node leaves「思考中」.
    const oneRun: ExecutionPlan = {
      id: "exec-1",
      planType: "multi_agent",
      taskSummary: "T",
      agents: [{ id: "agent-1", role: "修码员" }],
      runs: [{ id: "run-1", agentId: "agent-1", task: "修", dependsOn: [] }],
    };
    store().startExecution(oneRun, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    for (let i = 0; i < 8; i++) {
      store().recordFrame(
        {
          t: 1000 + i,
          kind: "run_reasoning_delta",
          runId: "run-1",
          agentId: "agent-1",
          delta: `chunk-${i}`,
        },
        MID,
      );
    }
    expect(rt().frames.length).toBeGreaterThan(2);
    store().setExecutionDetached(
      {
        execution_id: "exec-1",
        conversation_id: "cid",
        completed: 0,
        total: 1,
        host_turn_id: MID,
      },
      MID,
    );

    const settled: ExecutionJournal = {
      finishReason: "stop",
      events: [
        {
          type: "run_plan",
          timestamp: "2026-01-01T00:00:00.000Z",
          payload: {
            execution_id: "exec-1",
            plan_type: "multi_agent",
            task_summary: "T",
            agents: [{ id: "agent-1", role: "修码员" }],
            runs: [
              {
                id: "run-1",
                agent_id: "agent-1",
                task: "修",
                depends_on: [],
              },
            ],
          },
        },
        {
          type: "run_started",
          timestamp: "2026-01-01T00:00:01.000Z",
          payload: {
            agent_id: "agent-1",
            run_id: "run-1",
            parent_run_id: null,
            kind: "agent",
          },
        },
        {
          type: "run_completed",
          timestamp: "2026-01-01T00:00:02.000Z",
          payload: {
            run_id: "run-1",
            agent_id: "agent-1",
            output_summary: "done",
            duration_ms: 1000,
          },
        },
      ],
    };
    store().hydrateFromJournal(MID, settled);
    const exec = projectRuntime(rt());
    expect(exec?.runs.find((r) => r.id === "run-1")?.status).toBe("completed");
    // All workers settled → clear background stamp + mark completed.
    expect(rt().executionDetached).toBeNull();
    expect(rt().status).toBe("completed");
  });

  it("keeps executionDetached when hydrate settles one worker but others remain", () => {
    store().startExecution(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    store().recordFrame(started("agent-2", "run-2"), MID);
    for (let i = 0; i < 5; i++) {
      store().recordFrame(
        {
          t: 1000 + i,
          kind: "run_reasoning_delta",
          runId: "run-1",
          agentId: "agent-1",
          delta: `x${i}`,
        },
        MID,
      );
    }
    store().setExecutionDetached(
      {
        execution_id: "exec-1",
        conversation_id: "cid",
        completed: 0,
        total: 3,
        host_turn_id: MID,
      },
      MID,
    );

    const partial: ExecutionJournal = {
      finishReason: "stop",
      events: [
        {
          type: "run_plan",
          timestamp: "2026-01-01T00:00:00.000Z",
          payload: {
            execution_id: "exec-1",
            plan_type: "multi_agent",
            task_summary: plan.taskSummary,
            agents: plan.agents.map((a) => ({ id: a.id, role: a.role })),
            runs: plan.runs.map((r) => ({
              id: r.id,
              agent_id: r.agentId,
              task: r.task,
              depends_on: r.dependsOn,
            })),
          },
        },
        {
          type: "run_started",
          timestamp: "2026-01-01T00:00:01.000Z",
          payload: {
            agent_id: "agent-1",
            run_id: "run-1",
            parent_run_id: null,
            kind: "agent",
          },
        },
        {
          type: "run_completed",
          timestamp: "2026-01-01T00:00:02.000Z",
          payload: {
            run_id: "run-1",
            agent_id: "agent-1",
            output_summary: "fixer done",
            duration_ms: 500,
          },
        },
        {
          type: "run_started",
          timestamp: "2026-01-01T00:00:03.000Z",
          payload: {
            agent_id: "agent-2",
            run_id: "run-2",
            parent_run_id: null,
            kind: "agent",
          },
        },
      ],
    };
    store().hydrateFromJournal(MID, partial);
    const exec = projectRuntime(rt());
    expect(exec?.runs.find((r) => r.id === "run-1")?.status).toBe("completed");
    expect(exec?.runs.find((r) => r.id === "run-2")?.status).toBe("running");
    expect(rt().status).toBe("running");
    expect(rt().executionDetached?.execution_id).toBe("exec-1");
  });

  it("is idempotent when re-hydrating an equal journal", () => {
    store().hydrateFromJournal(MID, journal);
    expect(rt().plan?.runs).toHaveLength(1);
    expect(rt().frames).toHaveLength(2);
    store().hydrateFromJournal(MID, journal);
    expect(rt().plan?.runs).toHaveLength(1);
    expect(rt().frames).toHaveLength(2);
  });

  it("applies journal when it has more plan runs than the in-memory slot", () => {
    // Stale live after a missed graph_append: only batch-1 in memory; journal
    // carries the grown plan (batch-1 + append). Hydrate must catch up.
    const stale: ExecutionPlan = {
      id: "exec-1",
      planType: "multi_agent",
      taskSummary: "分析对比 React 和 Vue",
      agents: [{ id: "agent-1", role: "React 研究员" }],
      runs: [
        { id: "run-1", agentId: "agent-1", task: "研究 React", dependsOn: [] },
      ],
    };
    store().startExecution(stale, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);

    const grown: ExecutionJournal = {
      finishReason: "stop",
      events: [
        {
          type: "run_plan",
          timestamp: "2026-01-01T00:00:00.000Z",
          payload: {
            execution_id: "exec-1",
            plan_type: "multi_agent",
            task_summary: "分析对比 React 和 Vue",
            agents: [
              {
                id: "agent-1",
                role: "React 研究员",
              },
            ],
            runs: [
              {
                id: "run-1",
                agent_id: "agent-1",
                task: "研究 React",
                depends_on: [],
              },
            ],
          },
        },
        {
          type: "run_started",
          timestamp: "2026-01-01T00:00:01.000Z",
          payload: {
            agent_id: "agent-1",
            run_id: "run-1",
            parent_run_id: null,
            kind: "agent",
          },
        },
        {
          type: "run_plan",
          timestamp: "2026-01-01T00:00:02.000Z",
          payload: {
            execution_id: "exec-1",
            plan_type: "multi_agent",
            task_summary: "分析对比 React 和 Vue",
            agents: [{ id: "agent-2", role: "Vue 研究员" }],
            runs: [
              {
                id: "run-2",
                agent_id: "agent-2",
                task: "研究 Vue",
                depends_on: [],
              },
            ],
          },
        },
        {
          type: "run_started",
          timestamp: "2026-01-01T00:00:03.000Z",
          payload: {
            agent_id: "agent-2",
            run_id: "run-2",
            parent_run_id: null,
            kind: "agent",
          },
        },
      ],
    };
    store().hydrateFromJournal(MID, grown);
    expect(rt().plan?.runs.map((r) => r.id)).toEqual(["run-1", "run-2"]);
    expect(rt().plan?.agents.map((a) => a.id)).toEqual(["agent-1", "agent-2"]);
    expect(rt().frames).toHaveLength(2);
    expect(rt().status).toBe("completed");
  });

  it("draws nothing when the journal has no run_plan", () => {
    store().hydrateFromJournal(MID, { finishReason: "stop", events: [] });
    expect(rt().plan).toBeNull();
  });

  it("restores user_interjections on classic journal (no run_plan)", () => {
    store().hydrateFromJournal(MID, {
      finishReason: "stop",
      events: [
        {
          type: "user_interjection",
          timestamp: "2026-01-01T00:00:01.000Z",
          payload: {
            interjection_id: "inj-1",
            execution_id: "exec-classic",
            content: "改成用中文总结",
            status: "injected",
          },
        },
      ],
    });
    expect(rt().plan).toBeNull();
    expect(rt().userInterjections).toEqual([
      {
        interjectionId: "inj-1",
        executionId: "exec-classic",
        content: "改成用中文总结",
        status: "injected",
        note: null,
      },
    ]);
  });

  it("restores user_interjection agentMentions on journal replay", () => {
    store().hydrateFromJournal(MID, {
      finishReason: "stop",
      events: [
        {
          type: "user_interjection",
          timestamp: "2026-01-01T00:00:01.000Z",
          payload: {
            interjection_id: "inj-mention",
            execution_id: "exec-classic",
            content: "请让研究员再核一遍成本。",
            status: "received",
            agent_mentions: [{ agent_id: "agent_research", role: "研究员" }],
          },
        },
      ],
    });
    expect(rt().userInterjections).toEqual([
      {
        interjectionId: "inj-mention",
        executionId: "exec-classic",
        content: "请让研究员再核一遍成本。",
        status: "received",
        note: null,
        agentMentions: [{ agentId: "agent_research", role: "研究员" }],
      },
    ]);
  });
});

describe("ingestPlan (multi-batch delegate merge)", () => {
  // A second delegate batch *in the same turn* (adaptive D1′: the CEO delegates
  // again after seeing the first batch). Shares the execution id; run ids
  // are namespaced per delegate call so they never collide with batch 1.
  const batch2: ExecutionPlan = {
    id: "exec-1",
    planType: "multi_agent",
    taskSummary: "分析对比 React 和 Vue",
    agents: [{ id: "agent-3", role: "Svelte 研究员" }],
    runs: [
      { id: "run-4", agentId: "agent-3", task: "研究 Svelte", dependsOn: [] },
    ],
  };

  it("starts a fresh execution for the first batch of a turn", () => {
    store().ingestPlan(plan, MID);
    expect(rt().plan?.id).toBe("exec-1");
    expect(rt().plan?.runs).toHaveLength(3);
    expect(rt().status).toBe("running");
  });

  it("appends a later same-turn batch instead of resetting the graph", () => {
    store().ingestPlan(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    store().ingestPlan(batch2, MID);
    // New agent + run are appended; batch-1 nodes survive (the old bug wiped
    // them — only the last batch used to stay visible).
    expect(rt().plan?.agents.map((a) => a.id)).toEqual([
      "agent-1",
      "agent-2",
      "agent-3",
    ]);
    expect(rt().plan?.runs.map((s) => s.id)).toEqual([
      "run-1",
      "run-2",
      "run-3",
      "run-4",
    ]);
    // The batch-1 frame stream is preserved across the merge.
    expect(rt().frames).toHaveLength(1);
    // Presentation stamps: first plan = 委派 #1, appended plan = #2 (graph lanes).
    expect(rt().plan?.runs.map((s) => s.delegateBatch)).toEqual([1, 1, 1, 2]);
  });

  it("dedupes agents/runs already on the graph", () => {
    store().ingestPlan(plan, MID);
    store().ingestPlan(plan, MID);
    expect(rt().plan?.agents).toHaveLength(2);
    expect(rt().plan?.runs).toHaveLength(3);
  });

  it("resets the graph when a new turn's execution id differs", () => {
    store().ingestPlan(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    store().ingestPlan({ ...plan, id: "exec-2" }, MID);
    expect(rt().plan?.id).toBe("exec-2");
    expect(rt().frames).toEqual([]);
  });
});
describe("agent thinking display", () => {
  it("reasoningMeta collapses to thinking on/off", () => {
    expect(reasoningMeta(false).short).toBe("非思考");
    expect(reasoningMeta(true).short).toBe("思考");
    expect(reasoningMeta(true).label).toBe("思考");
  });

  it("projectExecution defaults thinking on", () => {
    const exec = projectExecution(plan, [], "running");
    const a1 = exec.agents.find((a) => a.id === "agent-1");
    expect(a1).toMatchObject({ thinking: true });
  });

  it("projectExecution honors explicit thinking on the plan", () => {
    const withThinking: ExecutionPlan = {
      ...plan,
      agents: [{ id: "agent-1", role: "R", thinking: false }],
      runs: [{ id: "run-1", agentId: "agent-1", task: "t", dependsOn: [] }],
    };
    const agent = projectExecution(withThinking, [], "running").agents[0];
    expect(agent).toMatchObject({ thinking: false });
  });
});

describe("worker tool_use_progress overlay", () => {
  it("overlays worker tool_use_progress onto the matching agent by run_id", () => {
    store().startExecution(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    store().setWorkerToolPhase(
      {
        tool_call_id: "tc-1",
        tool_name: "web_search",
        phase: "queued",
        run_id: "run-1",
      },
      MID,
    );
    const agent = projectRuntime(rt())?.agents.find((a) => a.id === "agent-1");
    expect(agent?.toolExecutionLive).toEqual({
      toolName: "web_search",
      phase: "queued",
    });

    store().setWorkerToolPhase(
      {
        tool_call_id: "tc-1",
        tool_name: "web_search",
        phase: "querying",
        run_id: "run-1",
      },
      MID,
    );
    const updated = projectRuntime(rt())?.agents.find(
      (a) => a.id === "agent-1",
    );
    expect(updated?.toolExecutionLive?.phase).toBe("querying");

    store().clearWorkerToolPhase("run-1", MID);
    const cleared = projectRuntime(rt())?.agents.find(
      (a) => a.id === "agent-1",
    );
    expect(cleared?.toolExecutionLive).toBeNull();
  });

  it("keeps worker tool phase overlay after execution_detached", () => {
    store().startExecution(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    store().setWorkerToolPhase(
      {
        tool_call_id: "tc-1",
        tool_name: "web_search",
        phase: "querying",
        run_id: "run-1",
      },
      MID,
    );
    store().setExecutionDetached(
      {
        execution_id: "exec-1",
        conversation_id: "c1",
        completed: 0,
        total: 1,
        host_turn_id: MID,
      },
      MID,
    );
    expect(rt().workerToolPhases["run-1"]).toEqual({
      phase: "querying",
      toolName: "web_search",
    });
    const agent = projectRuntime(rt())?.agents.find((a) => a.id === "agent-1");
    expect(agent?.toolExecutionLive).toEqual({
      toolName: "web_search",
      phase: "querying",
    });
  });

  it("ignores worker tool phase without run_id", () => {
    store().startExecution(plan, MID);
    store().recordFrame(started("agent-1", "run-1"), MID);
    store().setWorkerToolPhase(
      {
        tool_call_id: "tc-ceo",
        tool_name: "web_search",
        phase: "querying",
      },
      MID,
    );
    expect(rt().workerToolPhases).toEqual({});
  });
});

describe("team_synthesis_preview (CEO 协调模式 Phase 1)", () => {
  it("stores the latest preview on the runtime (transport-only)", () => {
    store().startExecution(plan, MID);
    expect(rt().teamSynthesisPreview).toBeNull();
    store().setTeamSynthesisPreview(
      {
        execution_id: "exec-1",
        completed: 1,
        total: 2,
        headline: "已完成 1/2：✅ React 研究员 ⏳ Vue 研究员",
        text: "已完成 1/2：✅ React 研究员 ⏳ Vue 研究员\n· React 研究员：ok",
        workers: [
          {
            run_id: "run-1",
            role: "React 研究员",
            status: "completed",
            summary: "ok",
          },
          {
            run_id: "run-2",
            role: "Vue 研究员",
            status: "pending",
            summary: "",
          },
        ],
        in_progress: true,
      },
      MID,
    );
    expect(rt().teamSynthesisPreview?.completed).toBe(1);
    expect(rt().teamSynthesisPreview?.headline).toContain("✅ React 研究员");
  });

  it("ignores preview when no plan is active", () => {
    store().setTeamSynthesisPreview(
      {
        execution_id: "x",
        completed: 0,
        total: 2,
        headline: "x",
        text: "x",
        workers: [],
        in_progress: true,
      },
      MID,
    );
    expect(rt().teamSynthesisPreview).toBeNull();
  });
});
