// @vitest-environment jsdom
import type { Conversation } from "@/stores/conversation";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useConversationBulkSelect } from "../useConversationBulkSelect";

const mocks = vi.hoisted(() => ({
  deleteAsync: vi.fn().mockResolvedValue(undefined),
  restoreMutate: vi.fn(),
  notifyConversationDeleted: vi.fn(),
  notifyError: vi.fn(),
}));

vi.mock("@/hooks/useConversations", () => ({
  useArchiveConversation: () => ({ mutateAsync: vi.fn() }),
  useDeleteConversation: () => ({ mutateAsync: mocks.deleteAsync }),
  useRestoreConversation: () => ({ mutate: mocks.restoreMutate }),
  useUnarchiveConversation: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/lib/conversationDeleteCopy", () => ({
  notifyConversationDeleted: (...args: unknown[]) =>
    mocks.notifyConversationDeleted(...args),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: (...args: unknown[]) => mocks.notifyError(...args),
}));

function conv(id: string): Conversation {
  return {
    id,
    title: id,
    updatedAt: "2026-08-01T00:00:00Z",
    messageCount: 1,
    lastMessagePreview: null,
  };
}

const LIST = [conv("c1"), conv("c2")];

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

function clickUndo() {
  const onUndo = mocks.notifyConversationDeleted.mock.calls[0]?.[1] as
    | (() => void)
    | undefined;
  onUndo?.();
}

beforeEach(() => {
  mocks.deleteAsync.mockReset();
  mocks.deleteAsync.mockResolvedValue(undefined);
  mocks.restoreMutate.mockReset();
  mocks.notifyConversationDeleted.mockReset();
  mocks.notifyError.mockReset();
});

describe("useConversationBulkSelect bulk delete", () => {
  it("deletes immediately and undo restores every successful id", async () => {
    const { result } = renderHook(
      () => useConversationBulkSelect(LIST, "__all__", false),
      { wrapper },
    );

    act(() => {
      result.current.setSelectMode(true);
      result.current.toggleSelectAll();
    });

    await act(async () => {
      await result.current.handleBulkDelete();
    });

    expect(mocks.deleteAsync).toHaveBeenCalledTimes(2);
    expect(mocks.deleteAsync).toHaveBeenNthCalledWith(1, "c1");
    expect(mocks.deleteAsync).toHaveBeenNthCalledWith(2, "c2");
    expect(mocks.notifyConversationDeleted).toHaveBeenCalledWith(
      "2 条",
      expect.any(Function),
    );
    expect(result.current.selectMode).toBe(false);

    clickUndo();
    expect(mocks.restoreMutate).toHaveBeenCalledTimes(2);
    expect(mocks.restoreMutate).toHaveBeenCalledWith("c1");
    expect(mocks.restoreMutate).toHaveBeenCalledWith("c2");
  });

  it("still offers undo for ids that succeeded before a mid-batch failure", async () => {
    mocks.deleteAsync.mockImplementation(async (id: string) => {
      if (id === "c2") throw new Error("busy");
    });

    const { result } = renderHook(
      () => useConversationBulkSelect(LIST, "__all__", false),
      { wrapper },
    );

    act(() => {
      result.current.setSelectMode(true);
      result.current.toggleSelectAll();
    });

    await act(async () => {
      await result.current.handleBulkDelete();
    });

    expect(mocks.notifyError).toHaveBeenCalled();
    expect(mocks.notifyConversationDeleted).toHaveBeenCalledWith(
      "1 条",
      expect.any(Function),
    );

    clickUndo();
    expect(mocks.restoreMutate).toHaveBeenCalledTimes(1);
    expect(mocks.restoreMutate).toHaveBeenCalledWith("c1");
    expect(mocks.restoreMutate).not.toHaveBeenCalledWith("c2");
  });
});
