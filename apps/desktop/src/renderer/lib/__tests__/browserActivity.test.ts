/**
 * 「浏览器」tab 存在性判定（conversationHasBrowserActivity）单测。
 *
 * 走真实 execution store 折叠（startExecution + recordFrame）而非手搓投影——判定的坐标系
 * （`assistantProjectionId` 键 + `agent.toolCalls`）必须与 sidePanel / 终端 tab 一致，手搓
 * 会把这条一致性测掉。覆盖：无活动 / worker 用过浏览器 / CEO process 直调 navigate /
 * 用完（turn 结束）仍为真 / 只按本会话消息计（不串其他会话）。
 */

import type { Message } from "@/stores/conversation/types";
import {
  type ExecutionPlan,
  type RunFrame,
  useExecutionStore,
} from "@/stores/execution";
import { beforeEach, describe, expect, it } from "vitest";
import {
  conversationHasBrowserActivity,
  conversationHasPendingBrowserLogin,
  conversationHasRunningTurn,
  isBrowserTool,
} from "../browserActivity";

const MID = "a1";

const plan: ExecutionPlan = {
  id: "exec-1",
  planType: "multi_agent",
  taskSummary: "查资料",
  agents: [{ id: "agent-1", role: "研究员" }],
  runs: [{ id: "run-1", agentId: "agent-1", task: "查", dependsOn: [] }],
};

/** 一条 assistant 消息（`assistantProjectionId` 无 serverMessageId 时落本地 id）。 */
function assistantMessage(id: string, process?: Message["process"]): Message {
  return {
    id,
    role: "assistant",
    content: "",
    createdAt: "2026-07-26T00:00:00.000Z",
    executionId: null,
    isStreaming: false,
    ...(process ? { process } : {}),
  };
}

/** 让 `messageId` 这一回合的 worker 调一次 `toolName`（可选 end 掉，模拟 turn 已结束）。 */
function seedWorkerToolCall(
  messageId: string,
  toolName: string,
  opts?: { end?: boolean },
): void {
  const exec = useExecutionStore.getState();
  const done: RunFrame[] = [
    {
      t: 3,
      kind: "tool_use_end",
      toolCallId: "tc-1",
      result: "ok",
      status: "success",
    },
    {
      t: 4,
      kind: "run_completed",
      runId: "run-1",
      agentId: "agent-1",
      outputSummary: "done",
      durationMs: 10,
    },
  ];
  exec.startExecution(plan, messageId);
  const frames: RunFrame[] = [
    {
      t: 1,
      kind: "run_started",
      agentId: "agent-1",
      runId: "run-1",
      parentRunId: null,
      runKind: "agent",
      continuesRunId: null,
    },
    {
      t: 2,
      kind: "tool_use_start",
      toolCallId: "tc-1",
      toolName,
      arguments: { url: "https://example.com" },
      runId: "run-1",
    },
    ...(opts?.end ? done : []),
  ];
  // Structural frames (incl. run_completed) go through recordFrame — the production
  // path that reconciles 收口. recordFrames is delta-only and does not settle.
  for (const frame of frames) {
    exec.recordFrame(frame, messageId);
  }
}

beforeEach(() => {
  useExecutionStore.setState({ byId: {} });
});

describe("isBrowserTool", () => {
  it("matches exact browser plus historical browser_* names", () => {
    expect(isBrowserTool("browser")).toBe(true);
    expect(isBrowserTool("browser_navigate")).toBe(true);
    expect(isBrowserTool("browser_screenshot")).toBe(true);
    expect(isBrowserTool("browser_console")).toBe(true);
    expect(isBrowserTool("web_fetch")).toBe(false);
    expect(isBrowserTool("web_search")).toBe(false);
  });
});

describe("conversationHasBrowserActivity", () => {
  it("is false with no messages / no execution", () => {
    expect(conversationHasBrowserActivity([], {})).toBe(false);
    expect(
      conversationHasBrowserActivity(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(false);
  });

  it("is false when the turn used other tools", () => {
    seedWorkerToolCall(MID, "web_search");
    expect(
      conversationHasBrowserActivity(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(false);
  });

  it("is true once a worker called a browser_* tool", () => {
    seedWorkerToolCall(MID, "browser_navigate");
    expect(
      conversationHasBrowserActivity(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(true);
  });

  it("is true once a worker called the unified browser tool", () => {
    seedWorkerToolCall(MID, "browser");
    expect(
      conversationHasBrowserActivity(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(true);
  });

  it("is true when CEO process has browser_navigate only", () => {
    expect(
      conversationHasBrowserActivity(
        [
          assistantMessage(MID, [
            {
              kind: "tool",
              id: "tc-ceo",
              tool_name: "browser_navigate",
              arguments: { url: "https://example.com" },
              result: null,
              status: "running",
            },
          ]),
        ],
        {},
      ),
    ).toBe(true);
  });

  it("is true when CEO process has unified browser only", () => {
    expect(
      conversationHasBrowserActivity(
        [
          assistantMessage(MID, [
            {
              kind: "tool",
              id: "tc-ceo",
              tool_name: "browser",
              arguments: { action: "navigate", url: "https://example.com" },
              result: null,
              status: "running",
            },
          ]),
        ],
        {},
      ),
    ).toBe(true);
  });

  it("is false when CEO process has only non-browser tools", () => {
    expect(
      conversationHasBrowserActivity(
        [
          assistantMessage(MID, [
            {
              kind: "tool",
              id: "tc-ceo",
              tool_name: "web_search",
              arguments: { query: "x" },
              result: null,
              status: "running",
            },
          ]),
        ],
        {},
      ),
    ).toBe(false);
  });

  it("stays true after the turn finished (入口窗口 ≠ running 窗口)", () => {
    seedWorkerToolCall(MID, "browser_navigate", { end: true });
    expect(
      conversationHasBrowserActivity(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(true);
  });

  it("only counts this conversation's messages", () => {
    seedWorkerToolCall("other-turn", "browser_navigate");
    // 本会话的消息里没有那一回合 → 判定为假（execution.byId 是跨会话全局表）。
    expect(
      conversationHasBrowserActivity(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(false);
  });
});

describe("conversationHasPendingBrowserLogin", () => {
  it("is false with no messages / no escalation", () => {
    expect(conversationHasPendingBrowserLogin([], {})).toBe(false);
    seedWorkerToolCall(MID, "browser_navigate");
    expect(
      conversationHasPendingBrowserLogin(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(false);
  });

  it("is true for a pending browserLogin escalate", () => {
    const plan: ExecutionPlan = {
      id: "exec-1",
      planType: "multi_agent",
      taskSummary: "登录",
      agents: [{ id: "agent-1", role: "研究员" }],
      runs: [{ id: "run-1", agentId: "agent-1", task: "登", dependsOn: [] }],
    };
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.recordFrames(
      [
        {
          t: 1,
          kind: "run_started",
          agentId: "agent-1",
          runId: "run-1",
          parentRunId: null,
          runKind: "agent",
          continuesRunId: null,
        },
        {
          t: 2,
          kind: "escalation_required",
          escalationId: "esc-login",
          runId: "run-1",
          agentId: "agent-1",
          question: "请登录",
          assumption: "已登",
          escalationKind: "normal",
          browserLogin: true,
        },
      ],
      MID,
    );
    expect(
      conversationHasPendingBrowserLogin(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(true);
  });

  it("is false for a pending escalate without browserLogin", () => {
    const plan: ExecutionPlan = {
      id: "exec-1",
      planType: "multi_agent",
      taskSummary: "拍板",
      agents: [{ id: "agent-1", role: "研究员" }],
      runs: [{ id: "run-1", agentId: "agent-1", task: "问", dependsOn: [] }],
    };
    const exec = useExecutionStore.getState();
    exec.startExecution(plan, MID);
    exec.recordFrames(
      [
        {
          t: 1,
          kind: "run_started",
          agentId: "agent-1",
          runId: "run-1",
          parentRunId: null,
          runKind: "agent",
          continuesRunId: null,
        },
        {
          t: 2,
          kind: "escalation_required",
          escalationId: "esc-1",
          runId: "run-1",
          agentId: "agent-1",
          question: "用哪个库？",
          assumption: "Postgres",
          escalationKind: "normal",
        },
      ],
      MID,
    );
    expect(
      conversationHasPendingBrowserLogin(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(false);
  });
});

describe("conversationHasRunningTurn", () => {
  it("is false with no messages / no execution", () => {
    expect(conversationHasRunningTurn([], {})).toBe(false);
    expect(
      conversationHasRunningTurn(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(false);
  });

  it("is true while an execution projection is running", () => {
    seedWorkerToolCall(MID, "browser_navigate");
    expect(
      conversationHasRunningTurn(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(true);
  });

  it("is false after the turn finished", () => {
    seedWorkerToolCall(MID, "browser_navigate", { end: true });
    expect(
      conversationHasRunningTurn(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(false);
  });

  it("only counts this conversation's messages", () => {
    seedWorkerToolCall("other-turn", "browser_navigate");
    expect(
      conversationHasRunningTurn(
        [assistantMessage(MID)],
        useExecutionStore.getState().byId,
      ),
    ).toBe(false);
  });
});
