import { handleExecutionEvent } from "@/services/sse/handlers/execution";
import * as refreshMod from "@/services/sse/refreshAfterBackgroundExecution";
import { useConversationStore } from "@/stores/conversation";
import {
  type ExecutionPlan,
  type RunFrame,
  execRuntime,
  useExecutionStore,
} from "@/stores/execution";
import { projectRuntime } from "@/stores/execution/hooks";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CID = "conv-exec-bg";
const MID = "srv-turn-bg";

const plan: ExecutionPlan = {
  id: "exec-bg",
  planType: "multi_agent",
  taskSummary: "后台调研",
  agents: [
    { id: "a1", role: "研究员" },
    { id: "a2", role: "撰写员" },
  ],
  runs: [
    { id: "r1", agentId: "a1", task: "调研", dependsOn: [] },
    { id: "r2", agentId: "a2", task: "撰写", dependsOn: [] },
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

function toolProgress(agentId: string, t = 2): RunFrame {
  return {
    t,
    kind: "run_tool_progress",
    agentId,
    toolName: "file_write",
    chars: 1200,
  };
}

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

const rt = () => execRuntime(useExecutionStore.getState(), MID);

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useExecutionStore.setState({ byId: {} });
  vi.spyOn(refreshMod, "refreshAfterBackgroundExecution").mockImplementation(
    () => {},
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("execution_detached / execution_completed live path", () => {
  it("detached → stamp executionDetached（后台态）", () => {
    seedTurn();
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.recordFrame(started("a1", "r1"), MID);

    handleExecutionEvent(
      {
        type: "execution_detached",
        timestamp: "",
        payload: {
          execution_id: "exec-bg",
          conversation_id: CID,
          completed: 0,
          total: 2,
          host_turn_id: MID,
        },
      },
      { conversationId: CID, source: "server" },
    );

    expect(rt().executionDetached).toEqual({
      execution_id: "exec-bg",
      conversation_id: CID,
      completed: 0,
      total: 2,
      host_turn_id: MID,
    });
    expect(rt().status).toBe("running");
    expect(refreshMod.refreshAfterBackgroundExecution).toHaveBeenCalledWith(
      CID,
    );
  });

  it("detached → keeps live toolProgress / workerToolPhases", () => {
    seedTurn();
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.recordFrame(started("a1", "r1"), MID);
    exec.recordFrame(toolProgress("a1"), MID);
    exec.setWorkerToolPhase(
      {
        tool_call_id: "c1",
        run_id: "r1",
        phase: "writing",
        tool_name: "file_write",
      },
      MID,
    );

    handleExecutionEvent(
      {
        type: "execution_detached",
        timestamp: "",
        payload: {
          execution_id: "exec-bg",
          conversation_id: CID,
          completed: 0,
          total: 2,
          host_turn_id: MID,
        },
      },
      { conversationId: CID, source: "server" },
    );

    expect(rt().workerToolPhases).toEqual({
      r1: { phase: "writing", toolName: "file_write" },
    });
    const after = projectRuntime(rt());
    const agent = after?.agents.find((a) => a.id === "a1");
    expect(agent?.toolProgress).toEqual({
      toolName: "file_write",
      chars: 1200,
    });
    expect(agent?.toolExecutionLive).toEqual({
      toolName: "file_write",
      phase: "writing",
    });
  });

  it("completed → 清后台、标完成、触发刷新", () => {
    seedTurn();
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.recordFrame(started("a1", "r1"), MID);
    exec.setExecutionDetached(
      {
        execution_id: "exec-bg",
        conversation_id: CID,
        completed: 1,
        total: 2,
        host_turn_id: MID,
      },
      MID,
    );

    handleExecutionEvent(
      {
        type: "execution_completed",
        timestamp: "",
        payload: {
          execution_id: "exec-bg",
          conversation_id: CID,
          completed: 2,
          total: 2,
          host_turn_id: MID,
        },
      },
      { conversationId: CID, source: "server" },
    );

    expect(rt().executionDetached).toBeNull();
    expect(rt().status).toBe("completed");
    expect(refreshMod.refreshAfterBackgroundExecution).toHaveBeenCalledWith(
      CID,
    );
  });

  it("status=cancelled → runtime cancelled（忠实跟 payload）", () => {
    seedTurn();
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.recordFrame(started("a1", "r1"), MID);
    exec.setExecutionDetached(
      {
        execution_id: "exec-bg",
        conversation_id: CID,
        completed: 1,
        total: 2,
        host_turn_id: MID,
      },
      MID,
    );

    handleExecutionEvent(
      {
        type: "execution_completed",
        timestamp: "",
        payload: {
          execution_id: "exec-bg",
          conversation_id: CID,
          completed: 1,
          total: 2,
          host_turn_id: MID,
          status: "cancelled",
        },
      },
      { conversationId: CID, source: "server" },
    );

    expect(rt().executionDetached).toBeNull();
    expect(rt().status).toBe("cancelled");
    expect(refreshMod.refreshAfterBackgroundExecution).toHaveBeenCalledWith(
      CID,
    );
  });

  it("failed 保留 executionDetached（失败与后台并陈）", () => {
    seedTurn();
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.setExecutionDetached(
      {
        execution_id: "exec-bg",
        conversation_id: CID,
        completed: 1,
        total: 2,
        host_turn_id: MID,
      },
      MID,
    );
    exec.setStatus("failed", MID);
    expect(rt().status).toBe("failed");
    expect(rt().executionDetached).toEqual({
      execution_id: "exec-bg",
      conversation_id: CID,
      completed: 1,
      total: 2,
      host_turn_id: MID,
    });
  });
});
