// @vitest-environment jsdom
/**
 * 页 AbortSignal 只取消该 effect 的窗 GET：cleanup 先 cancelled 再 abort，
 * overlay 只认 cancelled —— 切走不得把第二条对话打成「加载失败」。
 */
import { getRuntime, useConversationStore } from "@/stores/conversation";
import type { Message } from "@/stores/conversation";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchMessageWindow =
  vi.fn<typeof import("@/services/messages").fetchMessageWindow>();
const loadLatestWindow = vi.fn<
  typeof import("@/services/messages").loadLatestWindow
>(async () => true);
const jumpToMessage = vi.fn<typeof import("@/services/messages").jumpToMessage>(
  async () => {},
);
const scheduleHydrateAttachSettle =
  vi.fn<typeof import("@/services/turns").scheduleHydrateAttachSettle>();
const syncConversationFollow =
  vi.fn<
    typeof import("@/services/turns/conversationFollow").syncConversationFollow
  >();
const loadRecovery = vi.fn<typeof import("@/services/resume").loadRecovery>(
  async () => ({
    sidecarLive: false,
    cloudLive: false,
    cloudKnown: true,
    pausedCount: 0,
    unsynced: [],
  }),
);
const loadCachedConversation = vi.fn<
  typeof import("@/services/offlineCache").loadCachedConversation
>(async () => null);

vi.mock("@/components/chat/ChatView", () => ({ ChatView: () => null }));
vi.mock("@/components/layout/SidePanel", () => ({ SidePanel: () => null }));
vi.mock("@/components/layout/SidePanelToggle", () => ({
  SidePanelToggle: () => null,
}));
vi.mock("@/services/messages", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/messages")>();
  return {
    ...actual,
    fetchMessageWindow: (
      ...args: Parameters<typeof actual.fetchMessageWindow>
    ) => fetchMessageWindow(...args),
    loadLatestWindow: (...args: Parameters<typeof actual.loadLatestWindow>) =>
      loadLatestWindow(...args),
    jumpToMessage: (...args: Parameters<typeof actual.jumpToMessage>) =>
      jumpToMessage(...args),
  };
});
vi.mock("@/services/resume", () => ({
  loadRecovery: (
    ...args: Parameters<typeof import("@/services/resume").loadRecovery>
  ) => loadRecovery(...args),
}));
vi.mock("@/services/offlineCache", () => ({
  loadCachedConversation: (
    ...args: Parameters<
      typeof import("@/services/offlineCache").loadCachedConversation
    >
  ) => loadCachedConversation(...args),
  persistOpenedCache: vi.fn(async () => {}),
}));
vi.mock("@/services/turns", () => ({
  scheduleHydrateAttachSettle: (
    ...args: Parameters<
      typeof import("@/services/turns").scheduleHydrateAttachSettle
    >
  ) => scheduleHydrateAttachSettle(...args),
}));
vi.mock("@/services/turns/conversationFollow", () => ({
  syncConversationFollow: (
    ...args: Parameters<typeof syncConversationFollow>
  ) => syncConversationFollow(...args),
  stopAllConversationFollows: vi.fn(),
}));
vi.mock("@/stores/bookmarks", () => ({
  useBookmarkStore: Object.assign(
    (sel: (s: { hydrateForConversation: () => void }) => unknown) =>
      sel({ hydrateForConversation: () => {} }),
    { getState: () => ({ hydrateForConversation: () => {} }) },
  ),
}));
vi.mock("@/lib/log", () => ({ logEvent: vi.fn() }));
vi.mock("@/lib/detachLocalBrowserHost", () => ({
  detachLocalBrowserHost: vi.fn().mockResolvedValue(undefined),
}));

import { ConversationPage } from "../ConversationPage";

function hangUntilAborted(
  ...[_id, _query, signal]: Parameters<
    typeof import("@/services/messages").fetchMessageWindow
  >
): Promise<never> {
  return new Promise((_resolve, reject) => {
    const fail = () => reject(new DOMException("Aborted", "AbortError"));
    if (signal?.aborted) {
      fail();
      return;
    }
    signal?.addEventListener("abort", fail, { once: true });
  });
}

function Harness({ initial }: { initial: string }) {
  return (
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/c/:id" element={<ConversationPage />} />
      </Routes>
      <Switcher />
    </MemoryRouter>
  );
}

function Switcher() {
  const navigate = useNavigate();
  useEffect(() => {
    const t = window.setTimeout(() => navigate("/c/conv-b"), 20);
    return () => window.clearTimeout(t);
  }, [navigate]);
  return null;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

describe("ConversationPage hydrate abort", () => {
  beforeEach(() => {
    fetchMessageWindow.mockImplementation(hangUntilAborted);
    useConversationStore.setState({ currentConversationId: null, byId: {} });
  });

  it("cancelled abort does not paint 对话加载失败 on the next conversation", async () => {
    render(<Harness initial="/c/conv-a" />);
    expect(await screen.findByLabelText("正在加载对话")).toBeTruthy();

    await waitFor(() => {
      expect(syncConversationFollow).toHaveBeenCalledWith(null);
    });

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("对话加载失败")).toBeNull();
    expect(await screen.findByLabelText("正在加载对话")).toBeTruthy();
  });
});

function StaticHarness({ id }: { id: string }) {
  return (
    <MemoryRouter initialEntries={[`/c/${id}`]}>
      <Routes>
        <Route path="/c/:id" element={<ConversationPage />} />
      </Routes>
    </MemoryRouter>
  );
}

function bubble(
  id: string,
  role: "user" | "assistant",
  content: string,
  extra: Partial<Message> = {},
): Message {
  return {
    id,
    role,
    content,
    createdAt: "2026-08-15T00:00:00Z",
    executionId: extra.executionId ?? null,
    isStreaming: false,
    ...extra,
  };
}

describe("ConversationPage cold reconcile", () => {
  beforeEach(() => {
    useConversationStore.setState({ currentConversationId: null, byId: {} });
    loadCachedConversation.mockReset();
    fetchMessageWindow.mockReset();
    loadRecovery.mockResolvedValue({
      sidecarLive: false,
      cloudLive: false,
      cloudKnown: true,
      pausedCount: 0,
      unsynced: [],
    });
  });

  it("does not replace a cache that already has the team journal with a thinner GET", async () => {
    const thick = [
      bubble("u1", "user", "做 G1-C1"),
      bubble("a1", "assistant", "等待核验员", {
        executionId: "exec-1",
        process: [
          { kind: "team", execution_id: "exec-1" },
        ] as Message["process"],
        runs: {
          events: [
            { type: "run_plan", payload: { execution_id: "exec-1" } } as never,
          ],
          finishReason: "end_turn",
        },
      }),
    ];
    const thin = [
      bubble("u1", "user", "做 G1-C1"),
      bubble("a1", "assistant", "等待核验员"),
    ];
    loadCachedConversation.mockResolvedValue({
      conversation: {
        id: "conv-graph",
        title: "G1-C1",
        updatedAt: "2026-08-15T00:00:00Z",
        messageCount: 2,
        lastMessagePreview: "等待核验员",
        openedAt: 1,
        byteSize: 1,
      },
      messages: thick,
      memoryUpdates: [],
      hasMoreBefore: false,
      hasMoreAfter: false,
    });
    fetchMessageWindow.mockResolvedValue({
      messages: thin,
      total: 2,
      hasMoreBefore: false,
      hasMoreAfter: false,
      memoryUpdates: [],
    });

    render(<StaticHarness id="conv-graph" />);

    await waitFor(() => {
      expect(fetchMessageWindow).toHaveBeenCalled();
    });
    await waitFor(() => {
      const runs = getRuntime("conv-graph").messages.find(
        (m) => m.id === "a1",
      )?.runs;
      expect(runs?.events?.length).toBe(1);
    });
  });
});
