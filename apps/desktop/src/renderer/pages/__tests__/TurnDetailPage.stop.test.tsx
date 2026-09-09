// @vitest-environment jsdom
/**
 * 全屏回合详情顶栏只留返回与视图切换：不挂「停止生成」、不挂 taskSummary。
 */
import { useConversationStore } from "@/stores/conversation";
import type { Message } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import { useExecutionStore } from "@/stores/execution";
import type { ExecutionPlan } from "@/stores/execution";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/graph/GraphView", () => ({
  GraphView: () => <div data-testid="graph" />,
}));
vi.mock("@/components/layout/SidePanel", () => ({ SidePanel: () => null }));
vi.mock("@/components/layout/SidePanelToggle", () => ({
  SidePanelToggle: () => null,
}));
vi.mock("@/services/messages", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/messages")>();
  return {
    ...actual,
    fetchMessageWindow: vi.fn(async () => ({
      messages: [],
      memoryUpdates: [],
      hasMoreBefore: false,
      hasMoreAfter: false,
    })),
    ensureFullMessageRuns: vi.fn(async () => false),
  };
});
vi.mock("@/services/resume", () => ({
  loadRecovery: vi.fn(async () => ({
    sidecarLive: false,
    cloudLive: false,
    cloudKnown: true,
    pausedCount: 0,
    unsynced: [],
  })),
}));
vi.mock("@/services/offlineCache", () => ({
  loadCachedConversation: vi.fn(async () => null),
  persistOpenedCache: vi.fn(async () => {}),
}));
vi.mock("@/services/turns", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/turns")>();
  return {
    ...actual,
    scheduleHydrateAttachSettle: vi.fn(),
  };
});

import { TurnDetailPage } from "../TurnDetailPage";

const CID = "conv-live";
const TURN = "a1";
const ROSTER = "14 个 worker: 情报侦察、产品经理、安全基线、架构师";

const streamingAssistant: Message = {
  id: TURN,
  role: "assistant",
  content: "进行中",
  createdAt: "2026-09-10T00:00:00Z",
  executionId: "exec-1",
  isStreaming: true,
};

const TEAM_PLAN: ExecutionPlan = {
  id: "exec-1",
  planType: "multi_agent",
  taskSummary: ROSTER,
  agents: [{ id: "w1", role: "情报侦察" }],
  runs: [{ id: "r1", agentId: "w1", task: "查", dependsOn: [] }],
};

function renderLiveTurn() {
  useConversationStore.setState({
    currentConversationId: CID,
    byId: {
      [CID]: {
        ...EMPTY_RUNTIME,
        isGenerating: true,
        messages: [streamingAssistant],
      },
    },
  });
  useExecutionStore.getState().startExecution(TEAM_PLAN, TURN);
  render(
    <MemoryRouter initialEntries={[`/conversations/${CID}/turn/${TURN}`]}>
      <Routes>
        <Route
          path="/conversations/:id/turn/:turnId"
          element={<TurnDetailPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useExecutionStore.setState({ byId: {} });
});

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useExecutionStore.setState({ byId: {} });
});

describe("TurnDetailPage · 全屏顶栏铬条", () => {
  it("live 回合只有返回与视图切换，没有停止、没有任务摘要", async () => {
    renderLiveTurn();
    expect(await screen.findByRole("button", { name: "返回" })).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "协作图" })).toBeTruthy();
    });
    expect(screen.queryByRole("button", { name: "停止生成" })).toBeNull();
    expect(screen.queryByRole("button", { name: "停止" })).toBeNull();
    expect(screen.queryByText(ROSTER)).toBeNull();
  });
});
