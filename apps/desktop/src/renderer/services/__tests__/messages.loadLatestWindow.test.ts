/**
 * Whole-window write gates (step 1): residency + richer-only + generating +
 * active+hasMoreAfter soft refresh.
 * Step 4: trusted write refreshes offline opened cache; rejects do not.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/detachLocalBrowserHost", () => ({
  detachLocalBrowserHost: vi.fn().mockResolvedValue(undefined),
}));

const logEvent = vi.fn();
vi.mock("@/lib/log", () => ({
  logEvent: (...args: unknown[]) => logEvent(...args),
}));

const apiGet = vi.fn();
vi.mock("@/services/api", () => ({
  api: { get: (...args: unknown[]) => apiGet(...args) },
}));

const persistOpenedCache = vi.fn().mockResolvedValue(undefined);
vi.mock("@/services/offlineCache", () => ({
  persistOpenedCache: (...args: unknown[]) => persistOpenedCache(...args),
}));

import {
  getRuntime,
  isMessageWindowStrictlyRicher,
  useConversationStore,
} from "@/stores/conversation";
import type { Message } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { loadLatestWindow } from "../messages";
import {
  beginLocalConversationStream,
  resetStreamOwnershipForTests,
} from "../turns/streamOwnership";

const store = () => useConversationStore.getState();

function msg(
  id: string,
  role: "user" | "assistant",
  content: string,
  extra: Partial<Message> = {},
): Message {
  return {
    id,
    role,
    content,
    createdAt: "",
    executionId: null,
    isStreaming: false,
    ...extra,
  };
}

function mockWindow(
  messages: Message[],
  flags = { before: false, after: false },
) {
  apiGet.mockResolvedValueOnce({
    data: messages.map((m) => ({
      id: m.id,
      conversation_id: "a",
      role: m.role,
      content: m.content,
      reasoning_content: m.reasoning ?? null,
      created_at: m.createdAt || "2026-01-01T00:00:00Z",
      runs: m.runs ?? null,
    })),
    total: messages.length,
    has_more_before: flags.before,
    has_more_after: flags.after,
    memory_updates: [],
  });
}

beforeEach(() => {
  logEvent.mockClear();
  apiGet.mockReset();
  persistOpenedCache.mockClear();
  resetStreamOwnershipForTests();
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
    sliceLruOrder: [],
  });
  useInteractionStore.getState().clear();
});

describe("isMessageWindowStrictlyRicher", () => {
  it("rejects thinner / equal windows; accepts longer or same-id richer", () => {
    const thick = [
      msg("m1", "user", "first"),
      msg("m2", "assistant", "reply-full", {
        runs: {
          events: [{ type: "message_start" } as never],
          finishReason: "stop",
        },
      }),
    ];
    const thin = [msg("m1", "user", "first"), msg("m2", "assistant", "reply")];
    expect(isMessageWindowStrictlyRicher(thin, thick)).toBe(false);
    expect(isMessageWindowStrictlyRicher(thick, thick)).toBe(false);
    expect(isMessageWindowStrictlyRicher(thick, thin)).toBe(true);
    expect(
      isMessageWindowStrictlyRicher(
        [...thick, msg("m3", "user", "next")],
        thick,
      ),
    ).toBe(true);
  });
});

describe("loadLatestWindow write gates", () => {
  it("does not resurrect a slice after eviction (background soft refresh)", async () => {
    store().switchConversation("a");
    store().setMessageWindow(
      [
        msg("m1", "user", "first"),
        msg("m2", "assistant", "reply1"),
        msg("m3", "user", "second"),
        msg("m4", "assistant", "reply2-full"),
      ],
      { hasMoreBefore: false, hasMoreAfter: false },
      "a",
    );
    store().switchConversation("b");
    // Explicit eviction helper (simulates LRU drop); terminal SSE no longer
    // calls releaseBackgroundSlice (step 2).
    store().releaseBackgroundSlice("a");
    expect(store().byId.a).toBeUndefined();

    mockWindow([msg("m1", "user", "first"), msg("m2", "assistant", "reply1")]);
    await loadLatestWindow("a", { softRefresh: true });

    expect(store().byId.a).toBeUndefined();
    expect(apiGet).not.toHaveBeenCalled();
    expect(persistOpenedCache).not.toHaveBeenCalled();
    expect(logEvent).toHaveBeenCalledWith(
      "info",
      "conversation.slice_diag",
      expect.objectContaining({
        action: "reject_not_resident",
        conversation_id: "a",
      }),
    );
  });

  it("does not replace a thick local window with a thinner snapshot", async () => {
    store().switchConversation("a");
    const thick = [
      msg("m1", "user", "first"),
      msg("m2", "assistant", "reply1"),
      msg("m3", "user", "second"),
      msg("m4", "assistant", "reply2-full", {
        runs: {
          events: [
            { type: "run_plan" } as never,
            { type: "run_completed" } as never,
          ],
          finishReason: "stop",
        },
      }),
    ];
    store().setMessageWindow(
      thick,
      { hasMoreBefore: false, hasMoreAfter: false },
      "a",
    );

    mockWindow([
      msg("m1", "user", "first"),
      msg("m2", "assistant", "reply1"),
      msg("m3", "user", "second"),
      msg("m4", "assistant", "reply2"), // same ids, thinner content / no runs
    ]);
    await loadLatestWindow("a", { softRefresh: true });

    expect(getRuntime("a").messages.map((m) => m.id)).toEqual([
      "m1",
      "m2",
      "m3",
      "m4",
    ]);
    expect(getRuntime("a").messages.at(-1)?.content).toBe("reply2-full");
    expect(persistOpenedCache).not.toHaveBeenCalled();
    expect(logEvent).toHaveBeenCalledWith(
      "info",
      "conversation.slice_diag",
      expect.objectContaining({
        action: "reject_not_richer",
        conversation_id: "a",
      }),
    );
  });

  it("refuses whole-window replace while a local stream is pumping", async () => {
    store().switchConversation("a");
    store().setMessageWindow(
      [msg("m1", "user", "hi"), msg("m2", "assistant", "partial")],
      { hasMoreBefore: false, hasMoreAfter: false },
      "a",
    );
    const release = beginLocalConversationStream("a");

    mockWindow([
      msg("m1", "user", "hi"),
      msg("m2", "assistant", "partial"),
      msg("m3", "user", "next"),
    ]);
    await loadLatestWindow("a", { softRefresh: true });

    expect(getRuntime("a").messages).toHaveLength(2);
    expect(apiGet).not.toHaveBeenCalled();
    expect(persistOpenedCache).not.toHaveBeenCalled();
    expect(logEvent).toHaveBeenCalledWith(
      "info",
      "conversation.slice_diag",
      expect.objectContaining({
        action: "reject_generating",
        conversation_id: "a",
      }),
    );
    release();
  });

  it("allows whole-window replace when only follow-held isGenerating", async () => {
    store().switchConversation("a");
    store().setMessageWindow(
      [msg("m1", "user", "hi"), msg("m2", "assistant", "partial")],
      { hasMoreBefore: false, hasMoreAfter: false },
      "a",
    );
    store().setGenerating(true, "a");

    mockWindow([
      msg("m1", "user", "hi"),
      msg("m2", "assistant", "partial"),
      msg("m3", "user", "next"),
    ]);
    await expect(loadLatestWindow("a", { softRefresh: true })).resolves.toBe(
      true,
    );
    expect(getRuntime("a").messages).toHaveLength(3);
  });

  it("rejects soft refresh when active + hasMoreAfter (reading history)", async () => {
    store().switchConversation("a");
    store().setMessageWindow(
      [msg("m10", "user", "old"), msg("m11", "assistant", "old-reply")],
      { hasMoreBefore: true, hasMoreAfter: true },
      "a",
    );

    mockWindow([
      msg("m20", "user", "new"),
      msg("m21", "assistant", "new-reply"),
    ]);
    await loadLatestWindow("a", { softRefresh: true });

    expect(getRuntime("a").messages.map((m) => m.id)).toEqual(["m10", "m11"]);
    expect(getRuntime("a").hasMoreAfter).toBe(true);
    expect(apiGet).not.toHaveBeenCalled();
    expect(persistOpenedCache).not.toHaveBeenCalled();
    expect(logEvent).toHaveBeenCalledWith(
      "info",
      "conversation.slice_diag",
      expect.objectContaining({
        action: "reject_active_has_more_after",
        conversation_id: "a",
      }),
    );
  });

  it("allows intentional snap past hasMoreAfter (non-softRefresh)", async () => {
    store().switchConversation("a");
    store().setMessageWindow(
      [msg("m10", "user", "old"), msg("m11", "assistant", "old-reply")],
      { hasMoreBefore: true, hasMoreAfter: true },
      "a",
    );

    mockWindow([
      msg("m20", "user", "new"),
      msg("m21", "assistant", "new-reply"),
    ]);
    await expect(loadLatestWindow("a")).resolves.toBe(true);

    expect(getRuntime("a").messages.map((m) => m.id)).toEqual(["m20", "m21"]);
    expect(getRuntime("a").hasMoreAfter).toBe(false);
    expect(persistOpenedCache).toHaveBeenCalledTimes(1);
    expect(persistOpenedCache).toHaveBeenCalledWith(
      "a",
      expect.arrayContaining([
        expect.objectContaining({ id: "m20" }),
        expect.objectContaining({ id: "m21" }),
      ]),
      [],
      { hasMoreBefore: false, hasMoreAfter: false },
    );
  });

  it("does not persist an empty latest window", async () => {
    store().switchConversation("a");

    mockWindow([], { before: false, after: false });
    await expect(loadLatestWindow("a")).resolves.toBe(true);

    expect(getRuntime("a").messages).toHaveLength(0);
    expect(persistOpenedCache).not.toHaveBeenCalled();
  });

  it("persists opened cache after soft refresh richer write", async () => {
    store().switchConversation("a");
    store().setMessageWindow(
      [msg("m1", "user", "first")],
      { hasMoreBefore: false, hasMoreAfter: false },
      "a",
    );

    mockWindow([msg("m1", "user", "first"), msg("m2", "assistant", "reply")], {
      before: true,
      after: false,
    });
    await expect(loadLatestWindow("a", { softRefresh: true })).resolves.toBe(
      true,
    );

    expect(getRuntime("a").messages.map((m) => m.id)).toEqual(["m1", "m2"]);
    expect(persistOpenedCache).toHaveBeenCalledTimes(1);
    expect(persistOpenedCache).toHaveBeenCalledWith(
      "a",
      expect.arrayContaining([
        expect.objectContaining({ id: "m1" }),
        expect.objectContaining({ id: "m2" }),
      ]),
      [],
      { hasMoreBefore: true, hasMoreAfter: false },
    );
  });

  it("setMessageWindow itself refuses non-resident materialize", () => {
    store().switchConversation("b");
    store().setMessageWindow(
      [msg("m1", "user", "ghost")],
      { hasMoreBefore: false, hasMoreAfter: false },
      "a",
    );
    expect(store().byId.a).toBeUndefined();
    expect(logEvent).toHaveBeenCalledWith(
      "info",
      "conversation.slice_diag",
      expect.objectContaining({ action: "reject_not_resident" }),
    );
  });

  it("aborts the window GET when the page signal fires", async () => {
    store().switchConversation("a");
    const ac = new AbortController();
    apiGet.mockImplementation(
      (_path: unknown, init?: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          const onAbort = () =>
            reject(new DOMException("Aborted", "AbortError"));
          if (init?.signal?.aborted) onAbort();
          init?.signal?.addEventListener("abort", onAbort);
        }),
    );
    const pending = loadLatestWindow("a", { signal: ac.signal });
    ac.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(store().byId.a.messages).toEqual([]);
  });
});
