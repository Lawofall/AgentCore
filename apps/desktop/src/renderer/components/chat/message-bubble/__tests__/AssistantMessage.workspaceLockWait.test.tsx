// @vitest-environment jsdom
/**
 * workspace_lock_wait → 空气泡诚实等待态（不得静默等锁）。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/usage", () => ({
  useUsageStore: (
    sel: (s: {
      loadMessageCost: () => void;
      messageCosts: Record<string, never>;
    }) => unknown,
  ) => sel({ loadMessageCost: () => {}, messageCosts: {} }),
}));

import { useConversationStore } from "@/stores/conversation";
import type { Message } from "@/stores/conversation/types";
import { AssistantMessage } from "../AssistantMessage";

const CID = "conv-lock-wait";

function emptyStreamingAssistant(): Message {
  return {
    id: "a1",
    role: "assistant",
    content: "",
    createdAt: new Date().toISOString(),
    executionId: null,
    isStreaming: true,
  };
}

describe("AssistantMessage · workspace_lock_wait", () => {
  beforeEach(() => {
    useConversationStore.setState({
      currentConversationId: CID,
      byId: {
        [CID]: {
          messages: [emptyStreamingAssistant()],
          memoryUpdates: [],
          isGenerating: true,
          turnPhase: "streaming",
          abort: null,
          error: null,
          retry: null,
          errorAction: null,
          messageFocus: null,
          hasMoreBefore: false,
          hasMoreAfter: false,
          loadingOlder: false,
          loadingNewer: false,
          pendingTurnWarning: null,
          pendingTraceId: null,
          toolStartedMs: {},
          executionVia: null,
          waitingForWorkspaceLock: false,
          waitingForDeskProvision: false,
          ceoWindowTokens: null,
        },
      },
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("shows Thinking… when not waiting on workspace lock", () => {
    const msg = useConversationStore.getState().byId[CID].messages[0];
    render(
      <MemoryRouter>
        <AssistantMessage message={msg} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(screen.queryByText("等待工作区…")).toBeNull();
  });

  it("shows 正在准备云端环境 when waitingForDeskProvision", () => {
    useConversationStore.getState().setWaitingForDeskProvision(true, CID);
    const msg = useConversationStore.getState().byId[CID].messages[0];
    render(
      <MemoryRouter>
        <AssistantMessage message={msg} />
      </MemoryRouter>,
    );
    expect(screen.getByText("正在准备云端环境")).toBeTruthy();
    expect(screen.queryByText("Thinking…")).toBeNull();
  });

  it("does not show elapsed on empty Thinking", () => {
    const createdAt = new Date(Date.now() - 6_000).toISOString();
    useConversationStore.setState({
      byId: {
        ...useConversationStore.getState().byId,
        [CID]: {
          ...useConversationStore.getState().byId[CID],
          messages: [{ ...emptyStreamingAssistant(), createdAt }],
        },
      },
    });
    const { unmount } = render(
      <MemoryRouter>
        <AssistantMessage
          message={{ ...emptyStreamingAssistant(), createdAt }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(screen.queryByText("6s")).toBeNull();
    unmount();
  });
});
