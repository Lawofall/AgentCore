import type { MemoryUpdate } from "@/stores/conversation";
// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryUpdateCard } from "../MemoryUpdateCard";

/**
 * The conversation-tail card is the main way in to「这条不对」, but the surfaces that show
 * the result — 记忆动态 and its「已移走的记忆」list — live in another route with their own
 * cache. Rejecting a line here and finding nothing there is what this covers.
 */

const disputeMemoryLine = vi.fn();
const invalidateQueries = vi.fn();

vi.mock("@/services/memory", () => ({
  disputeMemoryLine: (...args: unknown[]) => disputeMemoryLine(...args),
  restoreMemoryLine: vi.fn(),
  moveMemoryBullet: vi.fn(),
  MEMORY_UPDATES_KEY: ["memory-updates"],
  MEMORY_DISPUTED_LINES_KEY: ["memory-disputed-lines"],
}));

vi.mock("@/lib/queryClient", () => ({
  queryClient: {
    invalidateQueries: (...a: unknown[]) => invalidateQueries(...a),
  },
}));

vi.mock("@/lib/toast", () => ({
  notifyInfo: vi.fn(),
  notifyActionError: vi.fn(),
}));

vi.mock("@/stores/disclosure", () => ({
  usePersistentDisclosure: () => [true, vi.fn()],
}));

vi.mock("@/hooks/useConversations", () => ({
  getConversations: () => [{ id: "c1", folderId: null, title: "t" }],
}));

vi.mock("@/hooks/useFolders", () => ({ getFolders: () => [] }));

vi.mock("@/stores/conversation", async () => {
  const actual = await vi.importActual<typeof import("@/stores/conversation")>(
    "@/stores/conversation",
  );
  return {
    ...actual,
    useConversationStore: (
      sel: (s: { currentConversationId: string }) => unknown,
    ) => sel({ currentConversationId: "c1" }),
  };
});

const update: MemoryUpdate = {
  id: "s1",
  createdAt: "2026-08-13T12:00:00Z",
  kind: "semantic",
  items: [
    {
      action: "add",
      file: "画像",
      section: "关于用户的事实",
      scope: "global",
      content: "用户在腾讯工作",
      target: "global/profile",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  disputeMemoryLine.mockResolvedValue({
    ok: true,
    conflict: false,
    version: "v2",
    lineId: "rec-1",
  });
});

describe("MemoryUpdateCard 这条不对", () => {
  it("invalidates the memory surfaces that show the result", async () => {
    render(
      <MemoryRouter>
        <MemoryUpdateCard update={update} />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByText("这条不对"));

    await waitFor(() => expect(disputeMemoryLine).toHaveBeenCalled());
    await waitFor(() =>
      expect(invalidateQueries).toHaveBeenCalledWith({
        queryKey: ["memory-disputed-lines"],
      }),
    );
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory-updates"],
    });
  });
});
