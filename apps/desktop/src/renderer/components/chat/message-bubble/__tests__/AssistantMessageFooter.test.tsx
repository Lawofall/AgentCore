// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Message } from "@/stores/conversation";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/conversation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/conversation")>();
  return {
    ...actual,
    useConversationStore: (
      sel: (s: { currentConversationId: string | null }) => unknown,
    ) => sel({ currentConversationId: "conv-1" }),
    getActiveRuntime: () => ({ messages: [] }),
    assistantProjectionId: (m: { id: string }) => m.id,
  };
});

vi.mock("@/lib/clipboard", () => ({
  copyText: vi.fn(async () => true),
}));

vi.mock("@/lib/toast", () => ({
  notifySuccess: vi.fn(),
  notifyError: vi.fn(),
}));

vi.mock("@/stores/bookmarks", () => ({
  useBookmarkStore: (
    sel: (s: { ids: Set<string>; toggle: () => void }) => unknown,
  ) => sel({ ids: new Set(), toggle: () => {} }),
}));

import { MessageMoreMenu } from "../AssistantMessageFooter";

const message: Message = {
  id: "asst-1",
  role: "assistant",
  content: "",
  createdAt: "2026-08-05T00:00:00Z",
  executionId: "exec-1",
  isStreaming: false,
  traceId: "trace-1",
};

afterEach(() => {
  cleanup();
});

describe("MessageMoreMenu 复制排查包", () => {
  it("打开更多后露出复制排查包", async () => {
    render(
      <TooltipProvider>
        <MessageMoreMenu message={message} captainContext={[]} />
      </TooltipProvider>,
    );
    const more = screen.getByRole("button", { name: "更多" });
    fireEvent.pointerDown(more);
    expect(
      await screen.findByRole("menuitem", { name: "复制排查包" }),
    ).toBeTruthy();
  });
});
