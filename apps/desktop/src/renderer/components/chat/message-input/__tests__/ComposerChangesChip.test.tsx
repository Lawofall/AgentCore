// @vitest-environment jsdom
/**
 * 输入区「改动」chip：有可回滚改动才亮，点开显式挂右坞，不因有货自动占条。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import { useConversationStore } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import type { Message } from "@/stores/conversation/types";
import { useExecutionStore } from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import type { ProcessStep } from "@/types/events";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { useLocalTurnBaselineIds } = vi.hoisted(() => ({
  useLocalTurnBaselineIds: vi.fn((): ReadonlySet<string> => new Set()),
}));

vi.mock("@/hooks/useLocalTurnBaselineIds", () => ({
  useLocalTurnBaselineIds,
}));

import { ComposerChangesChip } from "../ComposerChangesChip";

function writeStep(): ProcessStep {
  return {
    kind: "tool",
    id: "t-write",
    tool_name: "write",
    arguments: { file_path: "a.md", content: "x" },
    result: null,
    status: "success",
  };
}

function assistantWithWrite(): Message {
  return {
    id: "a1",
    role: "assistant",
    content: "ok",
    createdAt: "2026-08-10T10:00:00Z",
    executionId: null,
    isStreaming: false,
    process: [writeStep()],
  };
}

function setConversation(messages: Message[]): void {
  useConversationStore.setState({
    currentConversationId: "c1",
    byId: { c1: { ...EMPTY_RUNTIME, messages } },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ComposerChangesChip", () => {
  beforeEach(() => {
    useLocalTurnBaselineIds.mockReturnValue(new Set());
    useExecutionStore.setState({ byId: {} });
    useSidePanelStore.setState({
      changesOpen: false,
      floats: [],
    });
    setConversation([]);
  });

  it("is hidden when this conversation has not changed files", () => {
    setConversation([
      {
        id: "a1",
        role: "assistant",
        content: "hi",
        createdAt: "2026-08-10T10:00:00Z",
        executionId: null,
        isStreaming: false,
      },
    ]);
    render(
      <TooltipProvider>
        <ComposerChangesChip conversationId="c1" />
      </TooltipProvider>,
    );
    expect(screen.queryByTestId("composer-changes-chip")).toBeNull();
  });

  it("opens 改动 when clicked", () => {
    setConversation([assistantWithWrite()]);
    render(
      <TooltipProvider>
        <ComposerChangesChip conversationId="c1" />
      </TooltipProvider>,
    );

    fireEvent.click(screen.getByTestId("composer-changes-chip"));
    expect(useSidePanelStore.getState().changesOpen).toBe(true);
  });

  it("hides once 改动 is already open", () => {
    useSidePanelStore.setState({ changesOpen: true });
    setConversation([assistantWithWrite()]);
    render(
      <TooltipProvider>
        <ComposerChangesChip conversationId="c1" />
      </TooltipProvider>,
    );
    expect(screen.queryByTestId("composer-changes-chip")).toBeNull();
  });
});
