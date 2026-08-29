import type {
  CheckpointRequiredPayload,
  PlanReviewRequiredPayload,
} from "@/types/events";
import { beforeEach, describe, expect, it, vi } from "vitest";

const detachLocalBrowserHost = vi.fn().mockResolvedValue(undefined);
vi.mock("@/lib/detachLocalBrowserHost", () => ({
  detachLocalBrowserHost: (...args: unknown[]) =>
    detachLocalBrowserHost(...args),
}));

import { queryClient } from "@/lib/queryClient";
import { conversationKeys } from "@/lib/queryKeys";
import {
  CONVERSATION_SLICE_LRU_LIMIT,
  getActiveRuntime,
  getRuntime,
  useConversationStore,
} from "../conversation";
import { execRuntime, useExecutionStore } from "../execution";
import {
  entryToCheckpoint,
  entryToPlanReview,
  useInteractionStore,
} from "../interactions";
import { useQueuedTurnsStore } from "../queuedTurns";

const store = () => useConversationStore.getState();
const ix = () => useInteractionStore.getState();
function mustGet(id: string) {
  const entry = ix().get(id);
  expect(entry).toBeDefined();
  if (!entry) throw new Error(`expected interaction ${id}`);
  return entry;
}
/** Active conversation's runtime slice — runtime state is now keyed by id. */
const rt = () => getActiveRuntime();

beforeEach(() => {
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
    sliceLruOrder: [],
    pendingFocus: null,
  });
  useInteractionStore.getState().clear();
  detachLocalBrowserHost.mockClear();
  queryClient.clear();
});

describe("conversation store", () => {
  describe("switchConversation", () => {
    it("clears messages and sets current id", () => {
      store().addMessage({
        id: "m1",
        role: "user",
        content: "hello",
        createdAt: "",
        executionId: null,
        isStreaming: false,
      });
      store().setGenerating(true);

      store().switchConversation("conv-new");

      expect(store().currentConversationId).toBe("conv-new");
      expect(rt().messages).toEqual([]);
      expect(rt().isGenerating).toBe(false);
    });

    it("detaches Local browser host before switching", () => {
      store().switchConversation("conv-a");
      detachLocalBrowserHost.mockClear();
      store().switchConversation("conv-b");
      expect(detachLocalBrowserHost).toHaveBeenCalledTimes(1);
    });

    it("starts a fresh draft chat when switched to null", () => {
      store().switchConversation("conv-existing");
      store().addMessage({
        id: "m1",
        role: "user",
        content: "hello",
        createdAt: "",
        executionId: null,
        isStreaming: false,
      });
      store().setGenerating(true);

      store().switchConversation(null);

      expect(store().currentConversationId).toBeNull();
      expect(rt().messages).toEqual([]);
      expect(rt().isGenerating).toBe(false);
    });

    const keepAliveMsg = (id: string) => ({
      id,
      role: "user" as const,
      content: id,
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });

    it("clears leftover messageFocus when returning to A after visiting B", () => {
      store().switchConversation("a");
      store().addMessage(keepAliveMsg("m-a"));
      store().focusMessage("m-a");
      expect(store().byId.a?.messageFocus?.id).toBe("m-a");

      store().switchConversation("b");
      store().addMessage(keepAliveMsg("m-b"));
      store().switchConversation("a");

      expect(store().byId.a?.messageFocus).toBeNull();
    });

    it("clears leftover messageFocus on an LRU slice when switching into it", () => {
      store().switchConversation("a");
      store().addMessage(keepAliveMsg("m-a"));
      store().switchConversation("b");
      store().addMessage(keepAliveMsg("m-b"));
      store().focusMessage("m-b");
      expect(store().byId.b?.messageFocus?.id).toBe("m-b");

      store().switchConversation("a");
      expect(store().byId.b?.messageFocus?.id).toBe("m-b");

      store().switchConversation("b");
      expect(store().byId.b?.messageFocus).toBeNull();
    });

    it("does not clear messageFocus on same-id early return", () => {
      store().switchConversation("a");
      store().addMessage(keepAliveMsg("m-a"));
      store().focusMessage("m-a");
      store().switchConversation("a");
      expect(store().byId.a?.messageFocus?.id).toBe("m-a");
    });

    it("leaves pendingFocus intact when switching conversations", () => {
      store().requestMessageFocus("b", "msg-y");
      store().switchConversation("a");
      store().addMessage(keepAliveMsg("m-a"));
      store().switchConversation("b");
      expect(store().pendingFocus).toEqual({
        conversationId: "b",
        messageId: "msg-y",
      });
    });
  });

  // Step 4: switching no longer aborts the turn you leave. A live turn keeps
  // streaming into its own slice in the background; idle slices stay in an LRU
  // (warm reopen) instead of being dropped immediately.
  describe("switchConversation (background turns)", () => {
    const userMsg = {
      id: "m1",
      role: "user" as const,
      content: "hi",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    };

    it("keeps a generating conversation's slice alive when leaving it", () => {
      store().switchConversation("a");
      store().createAssistantMessage(); // byId.a: streaming, isGenerating
      store().switchConversation("b");
      // a's live turn survives — not aborted, not released.
      expect(store().byId.a?.isGenerating).toBe(true);
      expect(store().byId.a?.messages).toHaveLength(1);
    });

    it("keeps an idle conversation's buffer in LRU when leaving it", () => {
      store().switchConversation("a");
      store().addMessage(userMsg); // byId.a: idle (no live turn)
      store().switchConversation("b");
      // a is idle → retained for warm reopen (not dropped on leave).
      expect(store().byId.a?.messages).toHaveLength(1);
      expect(store().byId.a?.messages[0].content).toBe("hi");
    });

    it("returns to a live background turn without wiping its stream", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      store().appendToLastMessage("partial");
      store().switchConversation("b"); // a kept (busy)
      store().switchConversation("a"); // return to a
      expect(store().byId.a?.messages[0].content).toBe("partial");
      expect(store().byId.a?.isGenerating).toBe(true);
    });

    it("returns to an idle LRU slice as warm (messages intact)", () => {
      store().switchConversation("a");
      store().addMessage(userMsg);
      store().switchConversation("b");
      store().switchConversation("a");
      expect(store().byId.a?.messages[0].content).toBe("hi");
      expect(getRuntime("a").messages).toHaveLength(1);
    });
  });

  describe("switchConversation (slice LRU)", () => {
    const userMsg = {
      id: "m1",
      role: "user" as const,
      content: "hi",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    };

    it("evicts the oldest idle slice beyond CONVERSATION_SLICE_LRU_LIMIT", () => {
      // Open LIMIT+2 conversations with messages; active = last.
      // Idle retained = LIMIT most recent; oldest idle evicted.
      const ids = Array.from(
        { length: CONVERSATION_SLICE_LRU_LIMIT + 2 },
        (_, i) => `c${i}`,
      );
      for (const id of ids) {
        store().switchConversation(id);
        store().addMessage({ ...userMsg, id: `m-${id}`, content: id });
      }
      expect(store().byId.c0).toBeUndefined();
      for (let i = 1; i < ids.length; i++) {
        expect(store().byId[ids[i]]).toBeDefined();
      }
    });

    it("never evicts a busy slice even when idle cache is full", () => {
      store().switchConversation("busy");
      store().createAssistantMessage(); // generating
      for (let i = 0; i < CONVERSATION_SLICE_LRU_LIMIT + 1; i++) {
        const id = `idle-${i}`;
        store().switchConversation(id);
        store().addMessage({ ...userMsg, id: `m-${id}`, content: id });
      }
      expect(store().byId.busy?.isGenerating).toBe(true);
    });
  });

  // Step 6: the sidebar status dot (useConversationGenerating) reads each
  // conversation's *own* slice by id, not the active one — so a background turn
  // lights up its dot while the user looks at another conversation. getRuntime
  // is the imperative form of that selector (runtimeOf), so it covers the read.
  describe("per-conversation generating (sidebar status dot)", () => {
    const userMsg = {
      id: "m1",
      role: "user" as const,
      content: "hi",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    };

    it("reports a background conversation as generating while another is active", () => {
      store().switchConversation("a");
      store().createAssistantMessage(); // a is generating
      store().switchConversation("b"); // active = b, a kept alive (busy)

      expect(getRuntime("a").isGenerating).toBe(true);
      expect(getRuntime("b").isGenerating).toBe(false);
    });

    it("reports a background idle LRU conversation as not generating", () => {
      store().switchConversation("a");
      store().addMessage(userMsg); // a idle (no live turn)
      store().switchConversation("b"); // a retained in LRU
      expect(store().byId.a).toBeDefined();
      expect(getRuntime("a").isGenerating).toBe(false);
    });
  });

  // Step 2: terminal SSE no longer calls releaseBackgroundSlice — background
  // idle windows stay until LRU prune on switch. The API remains for tests /
  // explicit eviction (busy / active guards still apply).
  describe("releaseBackgroundSlice (explicit / test helper)", () => {
    it("background turn completion without release keeps the idle slice", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      store().switchConversation("b");
      store().finalizeLastMessage("a");
      expect(store().byId.a).toBeDefined();
      expect(getRuntime("a").isGenerating).toBe(false);
    });

    it("drops an idle background conversation's buffer when called", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      store().switchConversation("b");
      store().finalizeLastMessage("a");
      store().releaseBackgroundSlice("a");
      expect(store().byId.a).toBeUndefined();
    });

    it("never releases the active conversation", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      store().finalizeLastMessage();
      store().releaseBackgroundSlice("a");
      expect(store().byId.a).toBeDefined();
    });

    it("keeps a background slice that still has a pending approval", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      store().switchConversation("b");
      store().finalizeLastMessage("a");
      ix().upsertRequired({
        kind: "approval",
        conversationId: "a",
        messageId: "",
        payload: {
          approval_id: "x",
          conversation_id: "a",
          tool_call_id: "t",
          tool_name: "file_write",
          arguments: {},
        },
      });
      store().releaseBackgroundSlice("a");
      expect(store().byId.a).toBeDefined();
    });

    it("is a no-op for an unknown conversation", () => {
      store().switchConversation("a");
      store().releaseBackgroundSlice("ghost");
      expect(store().byId.a).toBeDefined();
    });
  });

  describe("addMessage", () => {
    it("appends a message to the list", () => {
      const msg = {
        id: "m1",
        role: "user" as const,
        content: "test",
        createdAt: "",
        executionId: null,
        isStreaming: false,
      };

      store().addMessage(msg);
      expect(rt().messages).toHaveLength(1);
      expect(rt().messages[0].content).toBe("test");
    });
  });

  describe("appendToLastMessage", () => {
    it("appends chunk to last message content", () => {
      store().addMessage({
        id: "m1",
        role: "assistant",
        content: "Hello",
        createdAt: "",
        executionId: null,
        isStreaming: true,
      });

      store().appendToLastMessage(" world");
      expect(rt().messages[0].content).toBe("Hello world");
    });

    it("does nothing when no messages", () => {
      store().appendToLastMessage("chunk");
      expect(rt().messages).toEqual([]);
    });
  });

  // The inline「思考·正文·工具」timeline (前端UX设计.md §一B): content folds into the
  // process step list (interleaved with reasoning/tools), in addition to keeping the
  // canonical message.content for copy / citations.
  describe("process timeline (inline 思考·正文·工具)", () => {
    it("folds content into a trailing content step after reasoning", () => {
      store().createAssistantMessage();
      store().appendReasoningToLastMessage("think");
      store().appendToLastMessage("answer");
      const msg = rt().messages[0];
      expect(msg.content).toBe("answer");
      expect(msg.process).toEqual([
        { kind: "reasoning", text: "think" },
        { kind: "content", text: "answer" },
      ]);
    });

    it("coalesces consecutive content deltas into one content step", () => {
      store().createAssistantMessage();
      store().appendToLastMessage("答");
      store().appendToLastMessage("案");
      const msg = rt().messages[0];
      expect(msg.content).toBe("答案");
      expect(msg.process).toEqual([{ kind: "content", text: "答案" }]);
    });

    // 交付前结构核验回炉（content_reset reason=finish_guard）：清空已流式的正文 +
    // 弹掉尾部 content 步，保留思考步，不折过程痕迹——让重写版替换草稿而非追加拼接。
    it("resetStreamingContent clears content + trailing content step, keeps reasoning", () => {
      store().createAssistantMessage();
      store().appendReasoningToLastMessage("先想一下");
      store().appendToLastMessage("草稿 [9]");
      store().resetStreamingContent("finish_guard");
      const msg = rt().messages[0];
      expect(msg.content).toBe("");
      expect(msg.process).toEqual([{ kind: "reasoning", text: "先想一下" }]);
    });

    // 所有 reason（含 retry）只清正文、不折过程痕迹。
    it("resetStreamingContent with reason=retry clears content without a process trace", () => {
      store().createAssistantMessage();
      store().appendReasoningToLastMessage("先想一下");
      store().appendToLastMessage("临时输出");
      store().resetStreamingContent("retry");
      const msg = rt().messages[0];
      expect(msg.content).toBe("");
      expect(msg.process).toEqual([{ kind: "reasoning", text: "先想一下" }]);
    });

    it("resetStreamingContent no-ops when the last message is not assistant", () => {
      store().addMessage({
        id: "u1",
        role: "user",
        content: "用户问题",
        createdAt: "",
        executionId: null,
        isStreaming: false,
      });
      store().resetStreamingContent("finish_guard");
      expect(rt().messages[0].content).toBe("用户问题");
    });
  });

  describe("attachErrorToLastMessage", () => {
    it("attaches a structured error to the last assistant message", () => {
      store().createAssistantMessage();
      store().appendToLastMessage("partial answer");

      store().attachErrorToLastMessage({
        code: "LLM_INSUFFICIENT_BALANCE",
        message: "当前模型 API Key 有效，但账户余额不足，请充值后重试。",
      });

      const last = rt().messages[0];
      expect(last.content).toBe("partial answer");
      expect(last.error?.code).toBe("LLM_INSUFFICIENT_BALANCE");
      expect(last.error?.message).toContain("余额不足");
    });

    it("does nothing when there is no assistant message", () => {
      store().attachErrorToLastMessage({ code: "X", message: "boom" });
      expect(rt().messages).toEqual([]);
    });
  });

  describe("createAssistantMessage", () => {
    it("creates an empty streaming assistant message", () => {
      const id = store().createAssistantMessage();

      expect(rt().messages).toHaveLength(1);
      expect(rt().messages[0].id).toBe(id);
      expect(rt().messages[0].role).toBe("assistant");
      expect(rt().messages[0].content).toBe("");
      expect(rt().messages[0].isStreaming).toBe(true);
      expect(rt().isGenerating).toBe(true);
    });
  });

  describe("finalizeLastMessage", () => {
    it("marks last message as non-streaming and clears isGenerating", () => {
      store().createAssistantMessage();
      store().appendToLastMessage("done");

      store().finalizeLastMessage();

      expect(rt().messages[0].isStreaming).toBe(false);
      expect(rt().isGenerating).toBe(false);
    });

    it("syncs sidebar lastMessagePreview from the closed reply", () => {
      queryClient.setQueryData(conversationKeys.grouped, {
        folders: [],
        conversations: [
          {
            id: "c1",
            title: "对话",
            updatedAt: "2020-01-01T00:00:00.000Z",
            messageCount: 1,
            lastMessagePreview: "旧摘要",
            folderId: null,
            localContainerRootId: null,
            localRootId: null,
            pinned: false,
            archived: false,
          },
        ],
      });
      store().switchConversation("c1");
      store().createAssistantMessage();
      store().appendToLastMessage("本回合新回复");
      store().finalizeLastMessage("c1");

      const row = queryClient.getQueryData<{
        conversations: { lastMessagePreview: string | null }[];
      }>(conversationKeys.grouped)?.conversations[0];
      expect(row?.lastMessagePreview).toBe("本回合新回复");
    });
  });

  describe("resumePausedAssistant / projection key", () => {
    it("reuses the paused bubble without creating a second assistant", () => {
      const clientId = store().createAssistantMessage();
      store().setServerMessageIdOnLastMessage("srv-1");
      store().finalizeLastMessage();
      expect(rt().messages).toHaveLength(1);
      expect(rt().messages[0].isStreaming).toBe(false);

      const found = store().resumePausedAssistant("srv-1");
      expect(found).toBe(clientId);
      expect(rt().messages).toHaveLength(1);
      expect(rt().messages[0].id).toBe(clientId);
      expect(rt().messages[0].serverMessageId).toBe("srv-1");
      expect(rt().messages[0].isStreaming).toBe(true);
      expect(rt().isGenerating).toBe(true);
    });

    it("aligns execution slot client→server on first stamp", () => {
      const clientId = store().createAssistantMessage();
      useExecutionStore.getState().startExecution(
        {
          id: "exec-1",
          planType: "multi_agent",
          taskSummary: "t",
          agents: [{ id: "a1", role: "r" }],
          runs: [{ id: "r1", agentId: "a1", task: "t", dependsOn: [] }],
        },
        clientId,
      );
      store().setServerMessageIdOnLastMessage("srv-align");
      expect(
        execRuntime(useExecutionStore.getState(), clientId).plan,
      ).toBeNull();
      expect(
        execRuntime(useExecutionStore.getState(), "srv-align").plan?.id,
      ).toBe("exec-1");
    });
  });

  describe("dropConversationRuntime", () => {
    const userMsg = {
      id: "m1",
      role: "user" as const,
      content: "hi",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    };

    it("forgets a conversation's runtime slice", () => {
      store().switchConversation("a");
      store().addMessage(userMsg);
      store().dropConversationRuntime("a");
      expect(store().byId.a).toBeUndefined();
    });

    it("clears currentConversationId when the dropped one was open", () => {
      store().switchConversation("a");
      store().dropConversationRuntime("a");
      expect(store().currentConversationId).toBeNull();
    });

    it("keeps current when a different conversation is dropped", () => {
      store().switchConversation("a");
      store().dropConversationRuntime("b");
      expect(store().currentConversationId).toBe("a");
    });
  });

  // Cursor-window state for the latest-window + infinite-scroll + load-around
  // model (载入模型 B): the window mutators that the message service drives.
  describe("cursor-window (load-around B)", () => {
    const mk = (id: string) => ({
      id,
      role: "user" as const,
      content: id,
      createdAt: id,
      executionId: null,
      isStreaming: false,
    });

    it("setMessageWindow replaces messages and sets both edge flags", () => {
      store().switchConversation("a");
      store().setMessageWindow(
        [mk("m2"), mk("m3")],
        { hasMoreBefore: true, hasMoreAfter: true },
        "a",
      );
      expect(rt().messages.map((m) => m.id)).toEqual(["m2", "m3"]);
      expect(rt().hasMoreBefore).toBe(true);
      expect(rt().hasMoreAfter).toBe(true);
    });

    it("prependMessages adds older messages and updates hasMoreBefore", () => {
      store().switchConversation("a");
      store().setMessageWindow(
        [mk("m3")],
        { hasMoreBefore: true, hasMoreAfter: false },
        "a",
      );
      store().prependMessages([mk("m1"), mk("m2")], false, "a");
      expect(rt().messages.map((m) => m.id)).toEqual(["m1", "m2", "m3"]);
      expect(rt().hasMoreBefore).toBe(false);
    });

    it("prependMessages dedupes ids already in the window", () => {
      store().switchConversation("a");
      store().setMessageWindow(
        [mk("m2"), mk("m3")],
        { hasMoreBefore: true, hasMoreAfter: false },
        "a",
      );
      store().prependMessages([mk("m1"), mk("m2")], false, "a");
      expect(rt().messages.map((m) => m.id)).toEqual(["m1", "m2", "m3"]);
    });

    it("appendNewerMessages adds newer history and updates hasMoreAfter", () => {
      store().switchConversation("a");
      store().setMessageWindow(
        [mk("m1")],
        { hasMoreBefore: false, hasMoreAfter: true },
        "a",
      );
      store().appendNewerMessages([mk("m2"), mk("m3")], false, "a");
      expect(rt().messages.map((m) => m.id)).toEqual(["m1", "m2", "m3"]);
      expect(rt().hasMoreAfter).toBe(false);
    });

    it("truncateAfter clears a stale hasMoreAfter (fork from history)", () => {
      store().switchConversation("a");
      store().setMessageWindow(
        [mk("m1"), mk("m2")],
        { hasMoreBefore: false, hasMoreAfter: true },
        "a",
      );
      store().truncateAfter("m1", "a");
      expect(rt().messages.map((m) => m.id)).toEqual(["m1"]);
      expect(rt().hasMoreAfter).toBe(false);
    });

    it("tracks per-direction loading flags", () => {
      store().switchConversation("a");
      store().setLoadingOlder(true, "a");
      store().setLoadingNewer(true, "a");
      expect(rt().loadingOlder).toBe(true);
      expect(rt().loadingNewer).toBe(true);
      store().setLoadingOlder(false, "a");
      expect(rt().loadingOlder).toBe(false);
      expect(rt().loadingNewer).toBe(true);
    });

    it("records and clears a pending cross-conversation focus", () => {
      store().requestMessageFocus("conv-x", "msg-y");
      expect(store().pendingFocus).toEqual({
        conversationId: "conv-x",
        messageId: "msg-y",
      });
      store().clearPendingFocus();
      expect(store().pendingFocus).toBeNull();
    });
  });
});

// 结构化挂起 2a (7.1): a plan_review card lives on the assistant message it paused —
// set live via InteractionStore; journal reload hydrates through
// hydrateInteractionsFromJournal (see interactions.test.ts).
describe("plan_review cards (结构化挂起 2a)", () => {
  const reqPayload = (
    id: string,
    runIds: string[],
  ): PlanReviewRequiredPayload => ({
    checkpoint_id: id,
    conversation_id: "a",
    steps: runIds.map((r) => ({
      run_id: r,
      role: `角色 ${r}`,
      summary: "产出",
    })),
    pending: [{ run_id: "next", role: "下游" }],
  });

  describe("InteractionStore plan_review + process stamp (live)", () => {
    it("upserts a pending card and stamps the process marker", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      const mid = rt().messages[0].id;
      const p = reqPayload("c1", ["run-1"]);
      ix().upsertRequired({
        kind: "plan_review",
        conversationId: "a",
        messageId: mid,
        payload: p as unknown as Record<string, unknown>,
      });
      store().stampPlanReviewMarker("c1", "a");
      expect(entryToPlanReview(mustGet("c1")).status).toBe("pending");
      expect(
        rt().messages[0].process?.some((s) => s.kind === "plan_review"),
      ).toBe(true);
    });

    it("dedupes a re-delivered required event", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      const mid = rt().messages[0].id;
      const p = reqPayload("c1", ["run-1"]);
      ix().upsertRequired({
        kind: "plan_review",
        conversationId: "a",
        messageId: mid,
        payload: p as unknown as Record<string, unknown>,
      });
      ix().upsertRequired({
        kind: "plan_review",
        conversationId: "a",
        messageId: mid,
        payload: p as unknown as Record<string, unknown>,
      });
      expect(
        [...ix().byId.values()].filter((e) => e.kind === "plan_review"),
      ).toHaveLength(1);
    });

    it("stamp is a no-op when there is no assistant message yet", () => {
      store().switchConversation("a");
      store().stampPlanReviewMarker("c1", "a");
      expect(rt().messages).toHaveLength(0);
    });

    it("markResolved flips the card to resolved", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      const mid = rt().messages[0].id;
      ix().upsertRequired({
        kind: "plan_review",
        conversationId: "a",
        messageId: mid,
        payload: reqPayload("c1", ["run-1"]) as unknown as Record<
          string,
          unknown
        >,
      });
      ix().markResolved({
        kind: "plan_review",
        id: "c1",
        resolution: { decision: "stop", note: "就此打住" },
      });
      expect(entryToPlanReview(mustGet("c1"))).toMatchObject({
        status: "resolved",
        decision: "stop",
        note: "就此打住",
      });
    });

    it("markResolved records an adjust decision + its steer note", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      const mid = rt().messages[0].id;
      ix().upsertRequired({
        kind: "plan_review",
        conversationId: "a",
        messageId: mid,
        payload: reqPayload("c1", ["run-1"]) as unknown as Record<
          string,
          unknown
        >,
      });
      ix().markResolved({
        kind: "plan_review",
        id: "c1",
        resolution: { decision: "adjust", note: "把重点放在风险上" },
      });
      expect(entryToPlanReview(mustGet("c1"))).toMatchObject({
        status: "resolved",
        decision: "adjust",
        note: "把重点放在风险上",
      });
    });
  });
});

// ask_user: the one asking surface (统一开场引导 + 途中拍板). A card lives on the
// assistant message it paused — set live via InteractionStore, flipped on resolve;
// journal reload hydrates through hydrateInteractionsFromJournal
// (see interactions.test.ts). The opening flavor carries the rich content the
// former kickoff did (assumptions / questions / style_options).
describe("ask_user cards (统一开场引导 + 途中拍板)", () => {
  const reqPayload = (id: string): CheckpointRequiredPayload => ({
    checkpoint_id: id,
    conversation_id: "a",
    question: "我先按这个方案做这个落地页，对吗？",
    assumptions: [{ id: "a0", label: "部署", value: "纯静态" }],
    questions: [
      {
        id: "q0",
        prompt: "主要给谁看？",
        kind: "choice",
        options: [
          { label: "潜在客户（推荐）", detail: "偏转化导向" },
          { label: "投资人" },
        ],
        multiple: false,
        default: "潜在客户（推荐）",
      },
    ],
  });

  describe("InteractionStore ask_user + process stamp (live)", () => {
    it("upserts a pending card and stamps the process marker", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      const mid = rt().messages[0].id;
      const p = reqPayload("c1");
      ix().upsertRequired({
        kind: "ask_user",
        conversationId: "a",
        messageId: mid,
        payload: p as unknown as Record<string, unknown>,
      });
      store().stampCheckpointMarker("c1", "a");
      expect(entryToCheckpoint(mustGet("c1"))).toMatchObject({
        id: "c1",
        status: "pending",
      });
      expect(
        rt().messages[0].process?.some((s) => s.kind === "checkpoint"),
      ).toBe(true);
    });

    it("dedupes a re-delivered required event", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      const mid = rt().messages[0].id;
      const p = reqPayload("c1");
      ix().upsertRequired({
        kind: "ask_user",
        conversationId: "a",
        messageId: mid,
        payload: p as unknown as Record<string, unknown>,
      });
      ix().upsertRequired({
        kind: "ask_user",
        conversationId: "a",
        messageId: mid,
        payload: p as unknown as Record<string, unknown>,
      });
      expect(
        [...ix().byId.values()].filter((e) => e.kind === "ask_user"),
      ).toHaveLength(1);
    });

    it("stamp is a no-op when there is no assistant message yet", () => {
      store().switchConversation("a");
      store().stampCheckpointMarker("c1", "a");
      expect(rt().messages).toHaveLength(0);
    });

    it("markResolved flips the card to resolved with the composed note", () => {
      store().switchConversation("a");
      store().createAssistantMessage();
      const mid = rt().messages[0].id;
      ix().upsertRequired({
        kind: "ask_user",
        conversationId: "a",
        messageId: mid,
        payload: reqPayload("c1") as unknown as Record<string, unknown>,
      });
      ix().markResolved({
        kind: "ask_user",
        id: "c1",
        resolution: {
          decision: "continue",
          note: "就按这个开做",
          selected: [],
        },
      });
      expect(entryToCheckpoint(mustGet("c1"))).toMatchObject({
        status: "resolved",
        decision: "continue",
        note: "就按这个开做",
      });
    });
  });

  it("setGenerating(false) holds while this conversation still has a local queue", () => {
    store().switchConversation("conv-queue-hold");
    store().setGenerating(true, "conv-queue-hold");
    useQueuedTurnsStore.getState().upsert({
      queueId: "q1",
      conversationId: "conv-queue-hold",
      content: "下一句",
      position: 1,
      queueDepth: 1,
    });
    store().setGenerating(false, "conv-queue-hold");
    expect(getRuntime("conv-queue-hold").isGenerating).toBe(true);
    useQueuedTurnsStore.setState({ byConversation: {} });
    store().setGenerating(false, "conv-queue-hold");
    expect(getRuntime("conv-queue-hold").isGenerating).toBe(false);
  });
});
