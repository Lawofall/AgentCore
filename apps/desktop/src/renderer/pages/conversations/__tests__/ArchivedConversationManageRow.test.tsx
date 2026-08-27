// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { notifyInfo } from "@/lib/toast";
import type { Conversation } from "@/stores/conversation";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  deleteAsync: vi.fn().mockResolvedValue(undefined),
  restore: vi.fn(),
}));

vi.mock("@/hooks/useConversations", () => ({
  useDeleteConversation: () => ({ mutateAsync: mocks.deleteAsync }),
  useRestoreConversation: () => ({ mutate: mocks.restore }),
  useUnarchiveConversation: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => [],
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyInfo: vi.fn(),
}));

vi.mock("@/stores/conversation", () => ({
  useConversationStore: (
    sel: (s: {
      currentConversationId: string | null;
      switchConversation: () => void;
      dropConversationRuntime: () => void;
    }) => unknown,
  ) =>
    sel({
      currentConversationId: null,
      switchConversation: vi.fn(),
      dropConversationRuntime: vi.fn(),
    }),
}));

import { ArchivedConversationManageRow } from "../ArchivedConversationManageRow";

const conv: Conversation = {
  id: "c1",
  title: "定价讨论",
  updatedAt: "2026-08-01T00:00:00Z",
  messageCount: 3,
  lastMessagePreview: "已归档旧稿。",
  archived: true,
};

function renderRow() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <ArchivedConversationManageRow conversation={conv} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

function rowGroup() {
  return screen.getByText("定价讨论").closest(".group") as HTMLElement;
}

beforeEach(() => {
  mocks.deleteAsync.mockReset();
  mocks.deleteAsync.mockResolvedValue(undefined);
  mocks.restore.mockReset();
  vi.mocked(notifyInfo).mockReset();
});

afterEach(cleanup);

describe("ArchivedConversationManageRow delete", () => {
  it("soft-deletes from the hover trash without a confirm step; undo restores to 已归档", async () => {
    renderRow();
    fireEvent.mouseEnter(rowGroup());
    fireEvent.click(screen.getByLabelText("删除对话"));

    await waitFor(() => expect(mocks.deleteAsync).toHaveBeenCalledWith("c1"));
    expect(screen.queryByLabelText("确认删除对话")).toBeNull();
    expect(screen.queryByLabelText("取消删除")).toBeNull();
    expect(notifyInfo).toHaveBeenCalledWith(
      "已删除对话",
      expect.objectContaining({
        description: "定价讨论",
        action: expect.objectContaining({ label: "撤销" }),
      }),
    );

    const opts = vi.mocked(notifyInfo).mock.calls[0]?.[1] as
      | { action?: { onClick?: () => void } }
      | undefined;
    opts?.action?.onClick?.();
    // Restore is the same mutation as live rows; the chat is still archived, so
    // it lands back on the 已归档 side rather than the live list.
    expect(mocks.restore).toHaveBeenCalledWith("c1");
  });
});
