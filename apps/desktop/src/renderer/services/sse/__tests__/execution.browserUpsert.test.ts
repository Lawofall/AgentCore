import { handleExecutionEvent } from "@/services/sse/handlers/execution";
import { useBrowserSessionsStore } from "@/stores/browserSessions";
import { useConversationStore } from "@/stores/conversation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CID = "conv-browser-upsert";

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useBrowserSessionsStore.setState({ pages: [], activePageId: null });
  useConversationStore.getState().switchConversation(CID);
  useConversationStore.getState().addMessage({
    id: "u1",
    role: "user",
    content: "go",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
  useConversationStore.getState().createAssistantMessage(CID);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("tool_use_end browser display → upsertServerSession", () => {
  it("upserts when display.kind=browser and session_id present", () => {
    useBrowserSessionsStore.getState().ensureBlankPage(CID);

    handleExecutionEvent(
      {
        type: "tool_use_end",
        timestamp: "",
        payload: {
          tool_call_id: "tc1",
          tool_name: "browser_navigate",
          result: "{}",
          status: "success",
          display: {
            kind: "browser",
            action: "navigate",
            url: "https://example.com/",
            title: "Example",
            session_id: "sess-1",
            host_kind: "local",
          },
        },
      },
      { conversationId: CID, source: "server" },
    );

    const page = useBrowserSessionsStore
      .getState()
      .pages.find((p) => p.serverSessionId === "sess-1");
    expect(page).toMatchObject({
      url: "https://example.com/",
      title: "Example",
      hostKind: "local",
      serverSessionId: "sess-1",
    });
    expect(useBrowserSessionsStore.getState().activePageId).toBe(page?.id);
  });

  it("upserts when tool_name is the unified browser", () => {
    useBrowserSessionsStore.getState().ensureBlankPage(CID);

    handleExecutionEvent(
      {
        type: "tool_use_end",
        timestamp: "",
        payload: {
          tool_call_id: "tc-unified",
          tool_name: "browser",
          result: "{}",
          status: "success",
          display: {
            kind: "browser",
            action: "click",
            url: "https://example.com/app",
            title: "App",
            session_id: "sess-unified",
            host_kind: "sandbox",
          },
        },
      },
      { conversationId: CID, source: "server" },
    );

    const page = useBrowserSessionsStore
      .getState()
      .pages.find((p) => p.serverSessionId === "sess-unified");
    expect(page).toMatchObject({
      url: "https://example.com/app",
      title: "App",
      hostKind: "sandbox",
      serverSessionId: "sess-unified",
    });
  });

  it("skips upsert when session_id missing", () => {
    handleExecutionEvent(
      {
        type: "tool_use_end",
        timestamp: "",
        payload: {
          tool_call_id: "tc2",
          tool_name: "browser_navigate",
          result: "{}",
          status: "success",
          display: {
            kind: "browser",
            action: "navigate",
            url: "https://example.com/",
          },
        },
      },
      { conversationId: CID, source: "server" },
    );

    expect(
      useBrowserSessionsStore.getState().pages.some((p) => p.serverSessionId),
    ).toBe(false);
  });
});
