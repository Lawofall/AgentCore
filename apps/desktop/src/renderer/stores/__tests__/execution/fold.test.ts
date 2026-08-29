import type { SSEEvent } from "@/types/events";
import { describe, expect, it } from "vitest";
import {
  type ExecutionPlan,
  type RunFrame,
  describeFrame,
  frameFromEvent,
  projectExecution,
} from "../../execution";
import { plan, started } from "./fixtures";

describe("projectExecution (fold)", () => {
  it("yields an all-pending snapshot from an empty frame stream", () => {
    const exec = projectExecution(plan, [], "running");
    expect(exec.runs.every((s) => s.status === "pending")).toBe(true);
    expect(exec.agents.every((a) => a.status === "idle")).toBe(true);
    expect(exec.progress).toEqual({ completed: 0, total: 3 });
    expect(exec.taskSummary).toBe("分析对比 React 和 Vue");
  });

  it("threads a plan-declared captain kind onto the run", () => {
    // The CEO 汇聚点 is identifiable from the plan alone — before its run_started
    // frame folds in — so the graph can adopt it as the real sink immediately.
    const withCaptain: ExecutionPlan = {
      ...plan,
      runs: [
        {
          id: "cap",
          agentId: "ceo",
          task: "",
          dependsOn: [],
          parentRunId: null,
          kind: "captain",
        },
        ...plan.runs,
      ],
    };
    const exec = projectExecution(withCaptain, [], "running");
    expect(exec.runs.find((s) => s.id === "cap")?.kind).toBe("captain");
    // Ordinary runs keep the default agent kind.
    expect(exec.runs.find((s) => s.id === "run-1")?.kind).toBe("agent");
  });

  it("marks run running and agent working on run_started", () => {
    const frames: RunFrame[] = [started("agent-1", "run-1")];
    const exec = projectExecution(plan, frames, "running");
    expect(exec.runs.find((s) => s.id === "run-1")?.status).toBe("running");
    const agent = exec.agents.find((a) => a.id === "agent-1");
    expect(agent?.status).toBe("working");
    expect(agent?.currentRunId).toBe("run-1");
    expect(exec.runs.find((s) => s.id === "run-2")?.status).toBe("pending");
  });

  it("folds run_phase with winding_down sticky over thinking/tool", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_phase",
        runId: "run-1",
        agentId: "agent-1",
        phase: "thinking",
      },
      {
        t: 3,
        kind: "run_phase",
        runId: "run-1",
        agentId: "agent-1",
        phase: "tool",
        toolName: "file_read",
      },
      {
        t: 4,
        kind: "run_phase",
        runId: "run-1",
        agentId: "agent-1",
        phase: "waiting_children",
      },
      {
        t: 5,
        kind: "run_phase",
        runId: "run-1",
        agentId: "agent-1",
        phase: "winding_down",
      },
      {
        t: 6,
        kind: "run_phase",
        runId: "run-1",
        agentId: "agent-1",
        phase: "thinking",
      },
      {
        t: 7,
        kind: "run_phase",
        runId: "run-1",
        agentId: "agent-1",
        phase: "tool",
        toolName: "handoff",
      },
    ];
    const run = projectExecution(plan, frames, "running").runs.find(
      (r) => r.id === "run-1",
    );
    expect(run?.phase).toBe("winding_down");
    expect(run?.phaseTool).toBeNull();

    const cleared = projectExecution(
      plan,
      [
        ...frames,
        {
          t: 8,
          kind: "run_completed",
          runId: "run-1",
          agentId: "agent-1",
          outputSummary: "done",
          durationMs: 10,
        },
      ],
      "running",
    ).runs.find((r) => r.id === "run-1");
    expect(cleared?.phase).toBeNull();
    expect(cleared?.phaseTool).toBeNull();
  });

  it("frameFromEvent maps run_phase payload", () => {
    const ev = {
      type: "run_phase",
      timestamp: "2026-01-01T00:00:00.000Z",
      payload: {
        run_id: "run-1",
        agent_id: "agent-1",
        phase: "waiting_children",
      },
    } as SSEEvent;
    expect(frameFromEvent(ev)).toMatchObject({
      kind: "run_phase",
      runId: "run-1",
      phase: "waiting_children",
    });
  });

  // attach 增量重放的帧级替换：wire 的 `replace` 必须过到帧上，否则整步全文会被当成增量追加。
  it("frameFromEvent carries run_output_delta.replace onto the frame", () => {
    const withReplace = frameFromEvent({
      type: "run_output_delta",
      timestamp: "2026-01-01T00:00:00.000Z",
      payload: {
        run_id: "run-1",
        agent_id: "agent-1",
        delta: "整步全文",
        replace: true,
      },
    } as SSEEvent);
    expect(withReplace).toMatchObject({
      kind: "run_output_delta",
      delta: "整步全文",
      replace: true,
    });
    // 直播帧不带标记 → 字段不出现（保持既有帧形逐字不变）。
    expect(
      frameFromEvent({
        type: "run_output_delta",
        timestamp: "2026-01-01T00:00:00.000Z",
        payload: { run_id: "run-1", agent_id: "agent-1", delta: "增量" },
      } as SSEEvent),
    ).not.toHaveProperty("replace");
  });

  it("accumulates streamed output deltas per agent", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_output_delta",
        runId: "run-1",
        agentId: "agent-1",
        delta: "Hello ",
      },
      {
        t: 3,
        kind: "run_output_delta",
        runId: "run-1",
        agentId: "agent-1",
        delta: "world",
      },
    ];
    const exec = projectExecution(plan, frames, "running");
    const agent = exec.agents.find((a) => a.id === "agent-1");
    expect(agent?.outputChunks.join("")).toBe("Hello world");
  });

  it("accumulates streamed reasoning deltas per agent (思考全文)", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_reasoning_delta",
        runId: "run-1",
        agentId: "agent-1",
        delta: "先拆解",
      },
      {
        t: 3,
        kind: "run_reasoning_delta",
        runId: "run-1",
        agentId: "agent-1",
        delta: "再对比",
      },
      // Reasoning is its own channel — it must not leak into the output text.
      {
        t: 4,
        kind: "run_output_delta",
        runId: "run-1",
        agentId: "agent-1",
        delta: "结论",
      },
    ];
    const exec = projectExecution(plan, frames, "running");
    const agent = exec.agents.find((a) => a.id === "agent-1");
    expect(agent?.reasoningChunks.join("")).toBe("先拆解再对比");
    expect(agent?.outputChunks.join("")).toBe("结论");
    // A worker that never streamed reasoning carries an empty log, not undefined.
    expect(
      exec.agents.find((a) => a.id === "agent-2")?.reasoningChunks,
    ).toEqual([]);
  });

  it("completes run with summary and duration on run_completed", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_completed",
        runId: "run-1",
        agentId: "agent-1",
        outputSummary: "React 优势分析完成",
        durationMs: 1500,
      },
    ];
    const exec = projectExecution(plan, frames, "running");
    const run = exec.runs.find((s) => s.id === "run-1");
    expect(run?.status).toBe("completed");
    expect(run?.outputSummary).toBe("React 优势分析完成");
    expect(run?.durationMs).toBe(1500);
    expect(exec.agents.find((a) => a.id === "agent-1")?.status).toBe(
      "completed",
    );
  });

  it("captures the failure reason on run_failed", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_failed",
        runId: "run-1",
        agentId: "agent-1",
        error: "工具超时：web_search",
      },
    ];
    const exec = projectExecution(plan, frames, "failed");
    const run = exec.runs.find((s) => s.id === "run-1");
    expect(run?.status).toBe("failed");
    expect(run?.error).toBe("工具超时：web_search");
    expect(exec.agents.find((a) => a.id === "agent-1")?.status).toBe("error");
    // Untouched runs carry no error.
    expect(exec.runs.find((s) => s.id === "run-2")?.error).toBeNull();
  });

  it("folds run_failed error_code / retryable / retry_after", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_failed",
        runId: "run-1",
        agentId: "agent-1",
        error: "上游限流，暂时无法继续本回合。请稍后再试。",
        errorCode: "LLM_RATE_LIMIT",
        retryable: true,
        retryAfter: 4,
        productLanded: true,
      },
    ];
    const exec = projectExecution(plan, frames, "failed");
    const run = exec.runs.find((s) => s.id === "run-1");
    expect(run?.errorCode).toBe("LLM_RATE_LIMIT");
    expect(run?.retryable).toBe(true);
    expect(run?.retryAfter).toBe(4);
    expect(run?.productLanded).toBe(true);
  });

  it("folds llm abort run_failed off running (not stuck executing)", () => {
    // B: engine.llm_failed_terminal / partial_failure → run_failed must leave the
    // graph node in failed (not permanent「执行中」), with a readable error.
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_tool_progress",
        agentId: "agent-1",
        toolName: "file_write",
        chars: 800,
      },
      {
        t: 3,
        kind: "run_failed",
        runId: "run-1",
        agentId: "agent-1",
        error: "模型响应中断，已保留已生成内容，可继续。",
      },
    ];
    const exec = projectExecution(plan, frames, "running");
    const run = exec.runs.find((s) => s.id === "run-1");
    expect(run?.status).toBe("failed");
    expect(run?.error).toContain("模型响应中断");
    const agent = exec.agents.find((a) => a.id === "agent-1");
    expect(agent?.status).toBe("error");
    expect(agent?.toolProgress).toBeNull();
  });

  it("names a hard-timeout kill 超时结束, not 已改方向", () => {
    // 硬超时强杀与「改派」共用取消通道；scrubber 只有 reason 能区分二者，说错就等于
    // 告诉用户有人给这名队员派了新活（其实是撞了时间上限被结束）。
    const timeoutKill: RunFrame = {
      t: 3,
      kind: "run_cancelled",
      runId: "run-1",
      agentId: "agent-1",
      reason: "worker_timeout",
    };
    expect(describeFrame(timeoutKill, plan)).toContain("超时结束");
    expect(
      describeFrame({ ...timeoutKill, reason: "redirect" }, plan),
    ).toContain("已改方向");
    expect(describeFrame({ ...timeoutKill, reason: "stop" }, plan)).toContain(
      "已停止",
    );
  });

  it("marks run and agent cancelled on run_cancelled", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_tool_progress",
        agentId: "agent-1",
        toolName: "file_write",
        chars: 40,
      },
      {
        t: 3,
        kind: "run_cancelled",
        runId: "run-1",
        agentId: "agent-1",
        reason: "redirect",
      },
    ];
    const exec = projectExecution(plan, frames, "running");
    const run = exec.runs.find((s) => s.id === "run-1");
    expect(run?.status).toBe("cancelled");
    const agent = exec.agents.find((a) => a.id === "agent-1");
    expect(agent?.status).toBe("cancelled");
    expect(agent?.toolProgress).toBeNull();
  });

  it("folds replacesRunId from run_started onto the node", () => {
    const frames: RunFrame[] = [
      {
        t: 1,
        kind: "run_started",
        agentId: "agent-1",
        runId: "run-1-redir",
        parentRunId: null,
        runKind: "agent",
        continuesRunId: null,
        replacesRunId: "run-1",
      },
    ];
    // Plan must declare the redir node for ensureRun to materialize it.
    const handoffPlan = {
      ...plan,
      runs: [
        ...plan.runs,
        {
          id: "run-1-redir",
          agentId: "agent-1",
          task: "调研（接手）",
          dependsOn: [],
          parentRunId: null,
          kind: "agent" as const,
          replacesRunId: null,
        },
      ],
    };
    const exec = projectExecution(handoffPlan, frames, "running");
    expect(exec.runs.find((s) => s.id === "run-1-redir")?.replacesRunId).toBe(
      "run-1",
    );
  });

  it("freezes in-flight nodes as cancelled when the run is stopped", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_completed",
        runId: "run-1",
        agentId: "agent-1",
        outputSummary: "done",
        durationMs: 1,
      },
      // run-2 / agent-2 are mid-flight (no terminal frame) when the user stops.
      started("agent-2", "run-2"),
    ];
    const exec = projectExecution(plan, frames, "cancelled");
    // Already-finished work is kept.
    expect(exec.runs.find((s) => s.id === "run-1")?.status).toBe("completed");
    expect(exec.agents.find((a) => a.id === "agent-1")?.status).toBe(
      "completed",
    );
    // In-flight work is frozen as cancelled — no live spinners after a stop.
    expect(exec.runs.find((s) => s.id === "run-2")?.status).toBe("cancelled");
    expect(exec.agents.find((a) => a.id === "agent-2")?.status).toBe(
      "cancelled",
    );
    // Never-started work closes as skipped —「未执行」, not forever「排队中」.
    expect(exec.runs.find((s) => s.id === "run-3")?.status).toBe("skipped");
  });

  // 08 NV-1: a turn that ends `failed` (hard crash / lost terminal frame) with a worker
  // still in-flight must NOT replay as a forever-spinning node on reload — the freeze
  // applies to `failed` too, not just `cancelled`.
  it("freezes in-flight nodes as cancelled when the run failed", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_completed",
        runId: "run-1",
        agentId: "agent-1",
        outputSummary: "done",
        durationMs: 1,
      },
      // run-2 / agent-2 are mid-flight (no terminal frame) when the turn errors out.
      started("agent-2", "run-2"),
    ];
    const exec = projectExecution(plan, frames, "failed");
    // Already-finished work is kept.
    expect(exec.runs.find((s) => s.id === "run-1")?.status).toBe("completed");
    // In-flight work is frozen as cancelled — no永久 spinner on a failed-turn reload.
    expect(exec.runs.find((s) => s.id === "run-2")?.status).toBe("cancelled");
    expect(exec.agents.find((a) => a.id === "agent-2")?.status).toBe(
      "cancelled",
    );
    // Never-started work closes as skipped —「未执行」, not forever「排队中」.
    expect(exec.runs.find((s) => s.id === "run-3")?.status).toBe("skipped");
  });

  it("captures the 阶段2 declaration slots (parentRunId/kind) from run_started", () => {
    // Defaulted from the plan: a flat 阶段1 worker is a top-level `agent`.
    const base = projectExecution(plan, [], "running").runs[0];
    expect(base.parentRunId).toBeNull();
    expect(base.kind).toBe("agent");
    // run_started carries whatever the wire declared onto the node, so a later
    // graph can style nested / captain runs without another fold change.
    const frames: RunFrame[] = [
      {
        t: 1,
        kind: "run_started",
        agentId: "agent-1",
        runId: "run-1",
        parentRunId: "del0_root",
        runKind: "captain",
        continuesRunId: null,
      },
    ];
    const run = projectExecution(plan, frames, "running").runs.find(
      (s) => s.id === "run-1",
    );
    expect(run?.parentRunId).toBe("del0_root");
    expect(run?.kind).toBe("captain");
  });

  it("derives progress from completed runs (run_progress is a marker)", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_completed",
        runId: "run-1",
        agentId: "agent-1",
        outputSummary: "done",
        durationMs: 1,
      },
      // Wire counters are ignored — progress folds from terminal run states so
      // it stays cumulative across multiple delegate batches.
      { t: 3, kind: "run_progress", completed: 99, total: 99 },
    ];
    expect(projectExecution(plan, frames, "running").progress).toEqual({
      completed: 1,
      total: 3,
    });
  });

  it("folds batch_metrics frames onto execution.batches (深层诊断指标)", () => {
    const snap = {
      nodes: 3,
      width: 4,
      peakRunning: 2,
      wallMs: 1000,
      busyMs: 1500,
      slotStarved: 1,
      completed: 3,
      failed: 0,
      skipped: 0,
      bindBoundaries: 0,
      scopeBoundaries: 1,
      checkpointBoundaries: 0,
      escalations: 2,
      scopeEscalations: 1,
      timeline: [
        { runId: "run-1", startMs: 0, endMs: 800, outcome: "completed" },
      ],
    };
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      { t: 2, kind: "batch_metrics", metrics: snap },
    ];
    // Empty before the frame, accrued after — and a second segment appends (a
    // checkpoint / scope yield + resume emits another snapshot).
    expect(projectExecution(plan, [], "running").batches).toEqual([]);
    const exec = projectExecution(plan, frames, "running");
    expect(exec.batches).toEqual([snap]);
    const two = projectExecution(
      plan,
      [
        ...frames,
        { t: 3, kind: "batch_metrics", metrics: { ...snap, nodes: 1 } },
      ],
      "running",
    );
    expect(two.batches.map((b) => b.nodes)).toEqual([3, 1]);
  });

  it("frameFromEvent maps batch_metrics (snake→camel snapshot)", () => {
    const frame = frameFromEvent({
      type: "batch_metrics",
      timestamp: "2026-01-01T00:00:00.000Z",
      payload: {
        execution_id: "exec-1",
        nodes: 5,
        width: 3,
        peak_running: 3,
        wall_ms: 2000,
        busy_ms: 4200,
        slot_starved: 2,
        completed: 4,
        failed: 1,
        skipped: 0,
        bind_boundaries: 1,
        scope_boundaries: 0,
        checkpoint_boundaries: 1,
        escalations: 0,
        scope_escalations: 0,
        timeline: [
          { run_id: "w1", start_ms: 0, end_ms: 1800, outcome: "completed" },
          { run_id: "w2", start_ms: 5, end_ms: 2000, outcome: "failed" },
        ],
      },
    });
    expect(frame).toEqual({
      t: Date.parse("2026-01-01T00:00:00.000Z"),
      kind: "batch_metrics",
      metrics: {
        nodes: 5,
        width: 3,
        peakRunning: 3,
        wallMs: 2000,
        busyMs: 4200,
        slotStarved: 2,
        completed: 4,
        failed: 1,
        skipped: 0,
        bindBoundaries: 1,
        scopeBoundaries: 0,
        checkpointBoundaries: 1,
        escalations: 0,
        scopeEscalations: 0,
        timeline: [
          { runId: "w1", startMs: 0, endMs: 1800, outcome: "completed" },
          { runId: "w2", startMs: 5, endMs: 2000, outcome: "failed" },
        ],
      },
    });
  });

  it("attaches tool calls to the running run's agent", () => {
    const frames: RunFrame[] = [
      started("agent-2", "run-2"),
      {
        t: 2,
        kind: "tool_use_start",
        toolCallId: "tc-1",
        toolName: "web_search",
        arguments: { query: "Vue" },
      },
      {
        t: 3,
        kind: "tool_use_end",
        toolCallId: "tc-1",
        result: "搜索结果…",
        status: "success",
      },
    ];
    const exec = projectExecution(plan, frames, "running");
    const agent2 = exec.agents.find((a) => a.id === "agent-2");
    expect(agent2?.toolCalls).toHaveLength(1);
    expect(agent2?.toolCalls[0].toolName).toBe("web_search");
    expect(agent2?.toolCalls[0].status).toBe("success");
    expect(agent2?.toolCalls[0].result).toBe("搜索结果…");
    expect(exec.agents.find((a) => a.id === "agent-1")?.toolCalls).toHaveLength(
      0,
    );
  });

  it("names a channel-redirect tool_use_end 改用搜索, not 工具失败", () => {
    expect(
      describeFrame(
        {
          t: 3,
          kind: "tool_use_end",
          toolCallId: "tc-1",
          result: "禁止用 code_execute 打开源码再正则扫描。",
          status: "redirect",
          failure: {
            message: "这一步想用脚本打开源码再搜索，没有执行。",
            code: "source_grep_redirect",
          },
        },
        plan,
      ),
    ).toBe("改用搜索");
  });

  // FE-003 (run_id 归属): a delegated worker tags its tool calls with `runId`.
  // Workers share the turn's top-level tool_use stream, so with width>1 a call
  // must land on ITS run's agent — not whichever run started first. (Before: the
  // fold dropped runId and attached every call to `runs.find(running)`, so the
  // first-started worker hogged all concurrent workers' tool rows.)
  it("attributes a worker tool call by runId under concurrency (width>1)", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1", 1),
      started("agent-2", "run-2", 2),
      // agent-1 started first, yet each call is tagged for a specific run.
      {
        t: 3,
        kind: "tool_use_start",
        toolCallId: "tc-2",
        toolName: "web_search",
        arguments: { query: "Vue" },
        runId: "run-2",
      },
      {
        t: 4,
        kind: "tool_use_start",
        toolCallId: "tc-1",
        toolName: "grep",
        arguments: { pattern: "React" },
        runId: "run-1",
      },
    ];
    const exec = projectExecution(plan, frames, "running");
    const a1 = exec.agents.find((a) => a.id === "agent-1");
    const a2 = exec.agents.find((a) => a.id === "agent-2");
    // Each call lands on its own run's agent — not both on the first-started one.
    expect(a1?.toolCalls.map((t) => t.toolName)).toEqual(["grep"]);
    expect(a2?.toolCalls.map((t) => t.toolName)).toEqual(["web_search"]);
  });

  // The captain's own calls carry no runId ("" on the wire) — they keep the
  // legacy running-run attribution, so a tagless call still lands (no regression).
  it("falls back to the running run for an untagged (captain) tool call", () => {
    const frames: RunFrame[] = [
      started("agent-2", "run-2"),
      {
        t: 2,
        kind: "tool_use_start",
        toolCallId: "tc-x",
        toolName: "web_search",
        arguments: {},
        runId: "",
      },
    ];
    const exec = projectExecution(plan, frames, "running");
    expect(exec.agents.find((a) => a.id === "agent-2")?.toolCalls).toHaveLength(
      1,
    );
    expect(exec.agents.find((a) => a.id === "agent-1")?.toolCalls).toHaveLength(
      0,
    );
  });

  it("is a pure prefix fold — replaying an earlier playhead drops later facts", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_output_delta",
        runId: "run-1",
        agentId: "agent-1",
        delta: "draft",
      },
      {
        t: 3,
        kind: "run_completed",
        runId: "run-1",
        agentId: "agent-1",
        outputSummary: "done",
        durationMs: 100,
      },
    ];

    // playhead = 1 frame applied → still running, no output yet.
    const early = projectExecution(plan, frames.slice(0, 1), "running");
    expect(early.runs.find((s) => s.id === "run-1")?.status).toBe("running");
    expect(early.agents.find((a) => a.id === "agent-1")?.outputChunks).toEqual(
      [],
    );

    // playhead = all frames → completed.
    const late = projectExecution(plan, frames, "running");
    expect(late.runs.find((s) => s.id === "run-1")?.status).toBe("completed");
  });

  // 升级实时可见: a worker's escalate folds onto its run (not the agent) so the node
  // carries a ⚠️ badge + the detail shows the 问题/假设 — surfaced the instant it fires.
  it("appends escalations onto the raising run, in fire order", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "run_escalation",
        runId: "run-1",
        agentId: "agent-1",
        question: "用 Postgres 还是 MySQL?",
        assumption: "暂用 Postgres",
        blocking: true,
        escalationId: "esc-raised-1",
        escalationKind: "normal",
      },
      {
        t: 3,
        kind: "run_completed",
        runId: "run-1",
        agentId: "agent-1",
        outputSummary: "done",
        durationMs: 100,
      },
    ];
    const exec = projectExecution(plan, frames, "running");
    const run1 = exec.runs.find((s) => s.id === "run-1");
    // Non-blocking: the run still completed despite escalating.
    expect(run1?.status).toBe("completed");
    // A non-blocking `raised` banner: no resolve target (id null), `raised` status, no answer.
    expect(run1?.escalations).toEqual([
      {
        id: "esc-raised-1",
        question: "用 Postgres 还是 MySQL?",
        assumption: "暂用 Postgres",
        blocking: true,
        status: "raised",
        answer: null,
        kind: "normal",
        questions: [],
      },
    ]);
    // A run that never escalated carries an empty list (drives "no badge").
    expect(exec.runs.find((s) => s.id === "run-2")?.escalations).toEqual([]);
  });

  it("maps a run_escalation event into an escalation frame", () => {
    const frame = frameFromEvent({
      type: "run_escalation",
      timestamp: "t",
      payload: {
        run_id: "run-1",
        agent_id: "agent-1",
        question: "Q?",
        assumption: "A",
        blocking: false,
      },
    } as SSEEvent);
    expect(frame).toEqual({
      t: expect.any(Number),
      kind: "run_escalation",
      runId: "run-1",
      agentId: "agent-1",
      question: "Q?",
      assumption: "A",
      blocking: false,
      escalationId: "",
      escalationKind: "normal",
    });
  });

  // 阻塞式求决策: the blocking-escalate pair (escalation_required → escalation_resolved)
  // folds onto the raising run's escalations[]. A worker is sequential ⇒ at most one pending
  // at a time (设计 §4.7); a pending one gates only its own worker, never a sibling.
  it("folds a blocking escalate: pending carries the resolve id, then resolved carries the answer", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "escalation_required",
        escalationId: "esc-1",
        runId: "run-1",
        agentId: "agent-1",
        question: "用哪个数据库？",
        assumption: "暂用 Postgres",
        escalationKind: "normal",
      },
    ];
    // Pending: a blocking escalation with its resolve id, status pending, no answer yet.
    expect(
      projectExecution(plan, frames, "running").runs.find(
        (s) => s.id === "run-1",
      )?.escalations,
    ).toEqual([
      {
        id: "esc-1",
        question: "用哪个数据库？",
        assumption: "暂用 Postgres",
        blocking: true,
        status: "pending",
        answer: null,
        kind: "normal",
        questions: [],
      },
    ]);
    frames.push({
      t: 3,
      kind: "escalation_resolved",
      escalationId: "esc-1",
      runId: "run-1",
      agentId: "agent-1",
      status: "resolved",
      answer: "用 Postgres。",
    });
    expect(
      projectExecution(plan, frames, "running").runs.find(
        (s) => s.id === "run-1",
      )?.escalations[0],
    ).toEqual({
      id: "esc-1",
      question: "用哪个数据库？",
      assumption: "暂用 Postgres",
      blocking: true,
      status: "resolved",
      answer: "用 Postgres。",
      kind: "normal",
      questions: [],
    });
  });

  // 结构化升级: a blocking escalate carrying structured `questions` (同 ask_user) folds them onto
  // the run's escalation so the EscalationCard renders the choice/text UI (not just a free-text box).
  it("folds a blocking escalate's structured questions onto the run", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "escalation_required",
        escalationId: "esc-1",
        runId: "run-1",
        agentId: "agent-1",
        question: "选型需要你拍板",
        assumption: "暂用 Postgres",
        escalationKind: "normal",
        questions: [
          {
            id: "q0",
            prompt: "用哪个数据库？",
            kind: "choice",
            options: [
              {
                label: "Postgres（推荐）",
                detail: "团队最熟，生态全",
              },
              { label: "MySQL" },
            ],
            multiple: false,
            default: "Postgres（推荐）",
          },
        ],
      },
    ];
    const esc = projectExecution(plan, frames, "running").runs.find(
      (s) => s.id === "run-1",
    )?.escalations[0];
    expect(esc?.questions).toEqual([
      {
        id: "q0",
        prompt: "用哪个数据库？",
        kind: "choice",
        options: [
          { label: "Postgres（推荐）", detail: "团队最熟，生态全" },
          { label: "MySQL" },
        ],
        multiple: false,
        default: "Postgres（推荐）",
      },
    ]);
  });

  it("folds browser_login onto RunEscalation.browserLogin", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "escalation_required",
        escalationId: "esc-login",
        runId: "run-1",
        agentId: "agent-1",
        question: "请在浏览器里登录后再继续",
        assumption: "用户已登录",
        escalationKind: "normal",
        browserLogin: true,
      },
    ];
    const esc = projectExecution(plan, frames, "running").runs.find(
      (s) => s.id === "run-1",
    )?.escalations[0];
    expect(esc).toMatchObject({
      id: "esc-login",
      status: "pending",
      browserLogin: true,
      question: "请在浏览器里登录后再继续",
    });
  });

  it("frameFromEvent maps wire browser_login → frame.browserLogin", () => {
    const frame = frameFromEvent({
      type: "escalation_required",
      timestamp: "t",
      payload: {
        escalation_id: "e1",
        run_id: "run-1",
        agent_id: "agent-1",
        question: "登录",
        assumption: "已登",
        browser_login: true,
      },
    } as SSEEvent);
    expect(frame).toMatchObject({
      kind: "escalation_required",
      browserLogin: true,
    });
  });

  // 等待口径（诚实性）: 默认部署无墙钟上限，wire 不带 timeout_seconds ⇒ 状态里也不能凭空出现
  // 一个「会自动继续」的口径；运维配了上限才折进来，供卡面照实写。
  it("folds the wire wait ceiling onto RunEscalation.timeoutSeconds, absent by default", () => {
    const base = {
      t: 2,
      kind: "escalation_required" as const,
      escalationId: "esc-1",
      runId: "run-1",
      agentId: "agent-1",
      question: "Q?",
      assumption: "暂用 A",
      escalationKind: "normal" as const,
    };
    const unlimited = projectExecution(
      plan,
      [started("agent-1", "run-1"), base],
      "running",
    ).runs.find((s) => s.id === "run-1")?.escalations[0];
    expect(unlimited?.timeoutSeconds).toBeUndefined();

    const capped = projectExecution(
      plan,
      [started("agent-1", "run-1"), { ...base, timeoutSeconds: 1800 }],
      "running",
    ).runs.find((s) => s.id === "run-1")?.escalations[0];
    expect(capped?.timeoutSeconds).toBe(1800);
  });

  it("frameFromEvent maps wire timeout_seconds → frame.timeoutSeconds", () => {
    const payload = {
      escalation_id: "e1",
      run_id: "run-1",
      agent_id: "agent-1",
      question: "Q?",
      assumption: "暂用 A",
    };
    expect(
      frameFromEvent({
        type: "escalation_required",
        timestamp: "t",
        payload,
      } as SSEEvent),
    ).not.toHaveProperty("timeoutSeconds");
    expect(
      frameFromEvent({
        type: "escalation_required",
        timestamp: "t",
        payload: { ...payload, timeout_seconds: 900 },
      } as SSEEvent),
    ).toMatchObject({ kind: "escalation_required", timeoutSeconds: 900 });
  });

  it("folds a blocking escalate timed_out: status timed_out, answer stays null", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "escalation_required",
        escalationId: "esc-1",
        runId: "run-1",
        agentId: "agent-1",
        question: "Q?",
        assumption: "暂用 A",
        escalationKind: "normal",
      },
      {
        t: 3,
        kind: "escalation_resolved",
        escalationId: "esc-1",
        runId: "run-1",
        agentId: "agent-1",
        status: "timed_out",
        answer: "",
      },
    ];
    const esc = projectExecution(plan, frames, "running").runs.find(
      (s) => s.id === "run-1",
    )?.escalations[0];
    expect(esc?.status).toBe("timed_out");
    expect(esc?.answer).toBeNull();
  });

  it("a pending blocking escalate does not halt a sibling run (non-halting)", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      started("agent-2", "run-2", 2),
      {
        t: 3,
        kind: "escalation_required",
        escalationId: "esc-1",
        runId: "run-1",
        agentId: "agent-1",
        question: "Q?",
        assumption: "A",
        escalationKind: "normal",
      },
    ];
    const exec = projectExecution(plan, frames, "running");
    // run-1 is parked on a pending escalation, but run-2 keeps running — the escalation gates
    // only its own worker, never the wave (区别于 approval/ask_user/plan_review 的 halting gate).
    expect(exec.runs.find((s) => s.id === "run-1")?.status).toBe("running");
    expect(exec.runs.find((s) => s.id === "run-1")?.escalations[0].status).toBe(
      "pending",
    );
    expect(exec.runs.find((s) => s.id === "run-2")?.status).toBe("running");
  });

  it("folds multiple sequential blocking escalates on one run independently", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "escalation_required",
        escalationId: "esc-1",
        runId: "run-1",
        agentId: "agent-1",
        question: "Q1?",
        assumption: "A1",
        escalationKind: "normal",
      },
      {
        t: 3,
        kind: "escalation_resolved",
        escalationId: "esc-1",
        runId: "run-1",
        agentId: "agent-1",
        status: "resolved",
        answer: "答1",
      },
      {
        t: 4,
        kind: "escalation_required",
        escalationId: "esc-2",
        runId: "run-1",
        agentId: "agent-1",
        question: "Q2?",
        assumption: "A2",
        escalationKind: "dep",
      },
      {
        t: 5,
        kind: "escalation_resolved",
        escalationId: "esc-2",
        runId: "run-1",
        agentId: "agent-1",
        status: "timed_out",
        answer: "",
      },
    ];
    // Each resolve matches by escalationId (not "first pending").
    expect(
      projectExecution(plan, frames, "running").runs.find(
        (s) => s.id === "run-1",
      )?.escalations,
    ).toEqual([
      {
        id: "esc-1",
        question: "Q1?",
        assumption: "A1",
        blocking: true,
        status: "resolved",
        answer: "答1",
        kind: "normal",
        questions: [],
      },
      {
        id: "esc-2",
        question: "Q2?",
        assumption: "A2",
        blocking: true,
        status: "timed_out",
        answer: null,
        kind: "dep",
        questions: [],
      },
    ]);
  });

  it("resolves by escalationId even when a later pending exists first in list", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "escalation_required",
        escalationId: "esc-old",
        runId: "run-1",
        agentId: "agent-1",
        question: "old?",
        assumption: "A",
        escalationKind: "normal",
      },
      {
        t: 3,
        kind: "escalation_required",
        escalationId: "esc-new",
        runId: "run-1",
        agentId: "agent-1",
        question: "new?",
        assumption: "B",
        escalationKind: "normal",
      },
      {
        t: 4,
        kind: "escalation_resolved",
        escalationId: "esc-new",
        runId: "run-1",
        agentId: "agent-1",
        status: "resolved",
        answer: "答新",
      },
    ];
    const escs = projectExecution(plan, frames, "running").runs.find(
      (s) => s.id === "run-1",
    )?.escalations;
    expect(escs?.find((e) => e.id === "esc-old")?.status).toBe("pending");
    expect(escs?.find((e) => e.id === "esc-new")).toMatchObject({
      status: "resolved",
      answer: "答新",
    });
  });

  it("ignores an escalation_resolved with no matching pending (safe no-op)", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      {
        t: 2,
        kind: "escalation_resolved",
        escalationId: "esc-ghost",
        runId: "run-1",
        agentId: "agent-1",
        status: "resolved",
        answer: "迟到的答复",
      },
    ];
    // A stale / duplicate resolve with nothing pending must not crash or fabricate an entry.
    expect(
      projectExecution(plan, frames, "running").runs.find(
        (s) => s.id === "run-1",
      )?.escalations,
    ).toEqual([]);
  });
});
