import { handleMessageStreamEvent } from "@/services/sse/handlers/messageStream";
import { useConversationStore } from "@/stores/conversation";
import {
  type ExecutionPlan,
  type RunFrame,
  execRuntime,
  useExecutionStore,
} from "@/stores/execution";
import { beforeEach, describe, expect, it } from "vitest";

// 后台托管继续跑 (coordination.turn_detached): the CEO reply's message_end can arrive
// while its team is still running detached-hosted in the background. The live
// handler must NOT collapse a graph with in-flight runs to `completed` — it holds
// `running` and lets the run-终态 reconcile close it when the last worker's terminal
// frame lands (delivered via re-attach replay / cross-turn append).

const CID = "conv-detached";
const MID = "srv-turn-detached";

const plan: ExecutionPlan = {
  id: "exec-1",
  planType: "multi_agent",
  taskSummary: "分析对比 React 和 Vue",
  agents: [
    { id: "a1", role: "React 研究员" },
    { id: "a2", role: "Vue 研究员" },
  ],
  runs: [
    { id: "r1", agentId: "a1", task: "研究 React", dependsOn: [] },
    { id: "r2", agentId: "a2", task: "研究 Vue", dependsOn: [] },
  ],
};

function started(agentId: string, runId: string, t = 1): RunFrame {
  return {
    t,
    kind: "run_started",
    agentId,
    runId,
    parentRunId: null,
    runKind: "agent",
    continuesRunId: null,
  };
}

function completed(agentId: string, runId: string, t = 2): RunFrame {
  return {
    t,
    kind: "run_completed",
    runId,
    agentId,
    outputSummary: "done",
    durationMs: 1,
  };
}

/** Seed a live CEO turn (user + streaming assistant stamped with a server id). */
function seedTurn(): void {
  const conv = useConversationStore.getState();
  conv.switchConversation(CID);
  conv.addMessage({
    id: "u1",
    role: "user",
    content: "go",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
  conv.createAssistantMessage(CID);
  conv.setServerMessageIdOnLastMessage(MID, CID);
}

function messageEnd(finishReason = "end_turn"): void {
  handleMessageStreamEvent(
    {
      type: "message_end",
      timestamp: "",
      payload: { finish_reason: finishReason },
    },
    { conversationId: CID, source: "server" },
  );
}

const rt = () => execRuntime(useExecutionStore.getState(), MID);

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useExecutionStore.setState({ byId: {} });
});

describe("message_end · detached-hosted graph keeps running", () => {
  it("keeps the graph running when it still has running/pending runs", () => {
    seedTurn();
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.recordFrame(started("a1", "r1"), MID); // r1 running, r2 pending
    expect(rt().status).toBe("running");

    messageEnd();

    // CEO 回合已结束，但团队仍在后台托管跑 → 不塌成 completed。
    expect(rt().status).toBe("running");
  });

  it("auto-completes once the detached workers' terminal frames arrive", () => {
    seedTurn();
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.recordFrame(started("a1", "r1"), MID);
    exec.recordFrame(started("a2", "r2"), MID); // both running at turn end

    messageEnd();
    expect(rt().status).toBe("running");

    // 托管 worker 终态帧陆续到齐（经重连回放 / 跨回合追加 → recordFrame）。
    exec.recordFrame(completed("a1", "r1"), MID);
    expect(rt().status).toBe("running"); // r2 仍在跑

    exec.recordFrame(completed("a2", "r2"), MID);
    // 全部 run 终态 → 既有 run 终态 reconcile 自动收口。
    expect(rt().status).toBe("completed");
  });

  it("still marks completed at message_end when every run is already terminal", () => {
    seedTurn();
    const single: ExecutionPlan = {
      ...plan,
      runs: [{ id: "r1", agentId: "a1", task: "研究 React", dependsOn: [] }],
    };
    useExecutionStore.getState().startExecution(single, MID);
    // 直接注入终态帧，绕过 recordFrame 的 reconcile —— 造出「status=running 但所有 run
    // 已终态」的槽，隔离验证 message_end 自身的收口门（全部终态 → 照标 completed）。
    useExecutionStore.setState((s) => ({
      byId: {
        ...s.byId,
        [MID]: {
          ...s.byId[MID],
          frames: [started("a1", "r1"), completed("a1", "r1")],
        },
      },
    }));
    expect(rt().status).toBe("running");

    messageEnd();

    expect(rt().status).toBe("completed");
  });

  it("marks completed at message_end when only captain is still pending", () => {
    // finalize HANDOFF / pre-plan captain run_started drop: workers terminal,
    // captain never folded — must not pin「正在收尾」after end_turn.
    seedTurn();
    const withCaptain: ExecutionPlan = {
      id: "exec-cap",
      planType: "multi_agent",
      taskSummary: "写 PPT",
      agents: [
        { id: "cap", role: "CEO" },
        { id: "a1", role: "脚本工程师" },
      ],
      runs: [
        {
          id: "cap",
          agentId: "cap",
          task: "",
          dependsOn: [],
          kind: "captain",
        },
        { id: "r1", agentId: "a1", task: "写脚本", dependsOn: [] },
      ],
    };
    useExecutionStore.getState().startExecution(withCaptain, MID);
    useExecutionStore.setState((s) => ({
      byId: {
        ...s.byId,
        [MID]: {
          ...s.byId[MID],
          frames: [started("a1", "r1"), completed("a1", "r1")],
        },
      },
    }));
    expect(rt().status).toBe("running");

    messageEnd();

    expect(rt().status).toBe("completed");
  });

  it("preserves the paused branch: message_end(paused) → paused despite in-flight runs", () => {
    seedTurn();
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.recordFrame(started("a1", "r1"), MID); // r1 running, r2 pending

    messageEnd("paused");

    expect(rt().status).toBe("paused");
  });

  it("leaves a failed graph failed (error path unaffected by the hold)", () => {
    seedTurn();
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.recordFrame(started("a1", "r1"), MID);
    exec.setStatus("failed", MID);

    messageEnd();

    expect(rt().status).toBe("failed");
  });
});
