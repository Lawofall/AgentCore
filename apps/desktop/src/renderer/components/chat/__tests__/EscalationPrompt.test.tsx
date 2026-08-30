// @vitest-environment jsdom
/**
 * 决策区 EscalationPrompt 铬条与 ResumePrompt / ApprovalPrompt / RunConfirmPrompt 对齐。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import { useConversationStore } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import {
  type ExecutionPlan,
  type RunFrame,
  useExecutionStore,
} from "@/stores/execution";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EscalationPrompt } from "../EscalationPrompt";

vi.mock("@/services/escalation", () => ({
  decideEscalation: vi.fn(),
}));

vi.mock("@/components/ManualHelpLink", () => ({
  MANUAL_HELP: { control: "/help" },
  ManualHelpLink: () => null,
}));

const CID = "conv-esc-prompt";
const MID = "msg-esc-prompt";

const plan: ExecutionPlan = {
  id: "exec-esc-prompt",
  planType: "multi_agent",
  taskSummary: "选型",
  agents: [{ id: "a1", role: "研究员" }],
  runs: [{ id: "r1", agentId: "a1", task: "调研", dependsOn: [] }],
};

const started: RunFrame = {
  t: 1,
  kind: "run_started",
  runId: "r1",
  agentId: "a1",
  parentRunId: null,
  runKind: "agent",
  continuesRunId: null,
};

const required: RunFrame = {
  t: 2,
  kind: "escalation_required",
  escalationId: "esc-1",
  runId: "r1",
  agentId: "a1",
  question: "数据库选 Postgres 还是 SQLite？",
  assumption: "暂按 Postgres 继续",
  escalationKind: "normal",
};

afterEach(cleanup);

beforeEach(() => {
  useExecutionStore.setState({ byId: {} });
  useConversationStore.setState({
    currentConversationId: CID,
    byId: {
      [CID]: {
        ...EMPTY_RUNTIME,
        messages: [
          {
            id: MID,
            role: "assistant",
            content: "",
            createdAt: "2026-01-01T00:00:00.000Z",
            executionId: "exec-esc-prompt",
            isStreaming: true,
          },
        ],
      },
    },
  } as never);
  useExecutionStore.getState().startExecution(plan, MID);
  useExecutionStore.getState().recordFrame(started, MID);
  useExecutionStore.getState().recordFrame(required, MID);
});

describe("EscalationPrompt 决策区铬条", () => {
  it("pending 卡外包 mx-4 mb-2（与 ResumePrompt / ApprovalPrompt / RunConfirmPrompt 同）", () => {
    const { container } = render(
      <MemoryRouter>
        <TooltipProvider>
          <EscalationPrompt />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText(/数据库选 Postgres 还是 SQLite/)).toBeTruthy();
    const chrome = container.querySelector(".mx-4.mb-2");
    expect(chrome).toBeTruthy();
    expect(chrome?.className).toMatch(/\bmx-4\b/);
    expect(chrome?.className).toMatch(/\bmb-2\b/);
  });
});
