import { TooltipProvider } from "@/components/ui/tooltip";
import { selectVisibleColdResumes } from "@/services/resume";
import { ensureStreamingAssistant } from "@/services/sse/contentBuffer";
import { handleInteractionEvent } from "@/services/sse/handlers/interaction";
import { handleMessageStreamEvent } from "@/services/sse/handlers/messageStream";
import { useConversationStore } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import type { SSEEvent } from "@/types/events";
// @vitest-environment jsdom
/**
 * Live cold card authority = InteractionStore.
 * leftover `team_preview_*` via SSE is consume-and-skip (no IX / no stamp);
 * leftover IX upserted directly still does not paint a clickable kickoff shell.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ResumePrompt } from "../ResumePrompt";

vi.mock("@/services/interactionSubmit", () => ({
  submitInteraction: vi.fn().mockResolvedValue("ok"),
  notifySubmitInteractionResult: vi.fn(),
  submitInteractionFeedback: (result: "busy" | "orphaned") =>
    result === "orphaned" ? "确认已失效" : "请稍候再试",
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

vi.mock("@/hooks/useModels", () => ({
  useModels: () => ({
    data: {
      models: [
        {
          id: "ceo-flash",
          display_name: "CEO Flash",
          origin: "platform",
          available: true,
        },
        {
          id: "worker-pro",
          display_name: "Worker Pro",
          origin: "platform",
          available: true,
        },
      ],
      current: { id: "ceo-flash", origin: "platform" },
    },
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("@/hooks/useLlmProviders", () => ({
  useLlmProviders: () => ({
    data: { providers: [], platform_available: true },
    isLoading: false,
    isError: false,
  }),
}));

const CID = "conv-live-ix";

const tpPayload = (
  checkpointId: string,
  over: Record<string, unknown> = {},
) => ({
  checkpoint_id: checkpointId,
  conversation_id: CID,
  primitive: "delegate" as const,
  workers: [
    { run_id: "r1", role: "研究员", task: "调研", depends_on: [] as string[] },
  ],
  tools: ["file_write"],
  motion: "",
  form: "",
  sides: [] as string[],
  max_rounds: 0,
  thorough: true,
  ...over,
});

function leftoverPreviewRequired(
  checkpointId: string,
  over: Record<string, unknown> = {},
): SSEEvent {
  return {
    type: "team_preview_required" as string,
    timestamp: "",
    payload: tpPayload(checkpointId, over),
  } as SSEEvent;
}

function renderResume() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <ResumePrompt />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  usePausedTurnStore.getState().clear();
  useInteractionStore.getState().clear();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

beforeEach(() => {
  usePausedTurnStore.getState().clear();
  useInteractionStore.getState().clear();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useConversationStore.getState().switchConversation(CID);
  useConversationStore.getState().addMessage({
    id: "u1",
    role: "user",
    content: "组团做定价",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
  useConversationStore.getState().addMessage({
    id: "client-uuid",
    role: "assistant",
    content: "",
    createdAt: "",
    executionId: null,
    isStreaming: true,
  });
  useConversationStore
    .getState()
    .setServerMessageIdOnLastMessage("m-server-tp", CID);
});

describe("ResumePrompt · live InteractionStore authority", () => {
  it("team_preview_required with stamp does not paint a clickable kickoff shell", () => {
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);

    useInteractionStore.getState().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "m-server-tp",
      origin: "server",
      payload: tpPayload("tp-live"),
    });

    const { container } = renderResume();

    expect(container.querySelector(".mx-4")).toBeNull();
    expect(screen.queryByText("此回合还停在开工确认")).toBeNull();
    expect(screen.queryByRole("button", { name: "继续" })).toBeNull();
    expect(screen.queryByRole("button", { name: "取消" })).toBeNull();
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
    expect(
      useInteractionStore.getState().listPending(CID, ["team_preview"]),
    ).toHaveLength(1);
  });

  it("does not paint clickable card before serverMessageId stamp", () => {
    useConversationStore.setState({ currentConversationId: null, byId: {} });
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "组团",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    useConversationStore.getState().addMessage({
      id: "client-only",
      role: "assistant",
      content: "",
      createdAt: "",
      executionId: null,
      isStreaming: true,
    });

    useInteractionStore.getState().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "client-only",
      origin: "sidecar",
      payload: tpPayload("tp-nostamp"),
    });

    const { container } = renderResume();
    expect(container.querySelector(".mx-4")).toBeNull();
    expect(screen.queryByText("继续")).toBeNull();
  });

  it("stamp + rekey still does not paint leftover team_preview", () => {
    useConversationStore.setState({ currentConversationId: null, byId: {} });
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "组团",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    useConversationStore.getState().addMessage({
      id: "client-late",
      role: "assistant",
      content: "",
      createdAt: "",
      executionId: null,
      isStreaming: true,
    });

    useInteractionStore.getState().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "client-late",
      origin: "server",
      payload: tpPayload("tp-late-stamp"),
    });

    renderResume();
    expect(screen.queryByText("继续")).toBeNull();

    act(() => {
      useConversationStore
        .getState()
        .setServerMessageIdOnLastMessage("m-server-late", CID);
    });

    expect(screen.queryByText("继续")).toBeNull();
    expect(screen.queryByText("此回合还停在开工确认")).toBeNull();
    expect(
      useInteractionStore.getState().byId.get("tp-late-stamp")?.messageId,
    ).toBe("m-server-late");
  });

  it("paints after stamp when pending arrived unbound (empty messageId)", () => {
    useConversationStore.setState({ currentConversationId: null, byId: {} });
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "组团",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    useConversationStore.getState().addMessage({
      id: "client-unbound",
      role: "assistant",
      content: "",
      createdAt: "",
      executionId: null,
      isStreaming: true,
    });

    useInteractionStore.getState().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: "",
      origin: "server",
      payload: {
        checkpoint_id: "cp-unbound",
        conversation_id: CID,
        question: "第二轮拍板？",
        assumptions: [],
        questions: [],
      },
    });

    renderResume();
    expect(screen.queryByText("第二轮拍板？")).toBeNull();

    act(() => {
      useConversationStore
        .getState()
        .setServerMessageIdOnLastMessage("m-server-unbound", CID);
    });

    expect(screen.getByText("第二轮拍板？")).toBeTruthy();
    expect(
      useInteractionStore.getState().byId.get("cp-unbound")?.messageId,
    ).toBe("m-server-unbound");
  });

  it("second-round leftover team_preview still does not paint", () => {
    useInteractionStore.getState().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "m-server-tp",
      origin: "server",
      payload: tpPayload("tp-round1"),
    });
    useInteractionStore.getState().markResolved({
      kind: "team_preview",
      id: "tp-round1",
      resolution: { decision: "continue" },
    });

    useConversationStore.getState().addMessage({
      id: "u2",
      role: "user",
      content: "再组一轮",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    useConversationStore.getState().addMessage({
      id: "client-r2",
      role: "assistant",
      content: "",
      createdAt: "",
      executionId: null,
      isStreaming: true,
    });
    useConversationStore
      .getState()
      .setServerMessageIdOnLastMessage("m-server-tp-r2", CID);

    useInteractionStore.getState().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "m-server-tp-r2",
      origin: "server",
      payload: tpPayload("tp-round2", {
        workers: [{ run_id: "r2", role: "写", task: "写", depends_on: [] }],
      }),
    });

    renderResume();
    expect(screen.queryByText("继续")).toBeNull();
    expect(screen.queryByText("此回合还停在开工确认")).toBeNull();
    expect(
      useInteractionStore.getState().listPending(CID, ["team_preview"]),
    ).toHaveLength(1);
  });

  it("second-round ask_user paints after first ask resolved", () => {
    useInteractionStore.getState().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: "m-server-tp",
      origin: "server",
      payload: {
        checkpoint_id: "cp-r1",
        conversation_id: CID,
        question: "第一轮？",
        assumptions: [],
        questions: [],
      },
    });
    useInteractionStore.getState().markResolved({
      kind: "ask_user",
      id: "cp-r1",
      resolution: { decision: "continue" },
    });

    useConversationStore.getState().addMessage({
      id: "u2",
      role: "user",
      content: "继续问",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    useConversationStore.getState().addMessage({
      id: "client-ask-r2",
      role: "assistant",
      content: "",
      createdAt: "",
      executionId: null,
      isStreaming: true,
    });
    useConversationStore
      .getState()
      .setServerMessageIdOnLastMessage("m-server-ask-r2", CID);

    useInteractionStore.getState().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: "m-server-ask-r2",
      origin: "server",
      payload: {
        checkpoint_id: "cp-r2",
        conversation_id: CID,
        question: "第二轮拍板？",
        assumptions: [],
        questions: [],
      },
    });

    renderResume();
    expect(screen.getByText("第二轮拍板？")).toBeTruthy();
    expect(screen.queryByText("第一轮？")).toBeNull();
  });

  it("keeps origin=sidecar on Interaction entry for submit routing", () => {
    useInteractionStore.getState().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "m-server-tp",
      origin: "sidecar",
      payload: tpPayload("tp-side"),
    });

    renderResume();
    expect(useInteractionStore.getState().byId.get("tp-side")?.origin).toBe(
      "sidecar",
    );
  });

  it("leftover team_preview does not paint; other cold cards still do", () => {
    useInteractionStore.getState().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: "m-server-tp",
      origin: "server",
      payload: {
        checkpoint_id: "cp-ask-keep",
        conversation_id: CID,
        question: "这次讨论怎么推进？",
        assumptions: [],
        questions: [],
      },
    });
    useInteractionStore.getState().upsertRequired({
      kind: "plan_review",
      conversationId: CID,
      messageId: "m-server-tp",
      origin: "server",
      payload: {
        checkpoint_id: "pr-keep",
        conversation_id: CID,
        steps: [{ run_id: "r1", role: "研" }],
        pending: [],
      },
    });
    // Older recovery shell — different checkpoint, would paint a second kickoff.
    usePausedTurnStore.getState().addLiveResume({
      messageId: "m-server-tp",
      conversationId: CID,
      checkpointId: "tp-paused-old",
      kind: "team_preview",
      userMessage: "组团做定价",
      userMessageId: "u1",
      steps: [],
      pending: [],
      workers: [{ run_id: "old", role: "旧", task: "旧", depends_on: [] }],
      tools: [],
      primitive: "delegate",
      headline: "旧开工卡 · 预计 1 人",
      motion: "",
      form: "",
      sides: [],
      maxRounds: 0,
      thorough: true,
      question: "",
      assumptions: [],
      questions: [],
      intent: "kickoff",
      origin: "server",
    });
    useInteractionStore.getState().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "m-server-tp",
      origin: "server",
      payload: tpPayload("tp-old-ix", { headline: "旧 IX 开工卡" }),
    });
    useInteractionStore.getState().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "m-server-tp",
      origin: "server",
      payload: tpPayload("tp-latest", { headline: "最新开工卡" }),
    });

    const visible = selectVisibleColdResumes({
      conversationId: CID,
      byId: useInteractionStore.getState().byId,
      pausedPending: usePausedTurnStore.getState().pending,
      messages: useConversationStore.getState().byId[CID]?.messages ?? [],
    });
    expect(visible.map((v) => v.kind).sort()).toEqual([
      "ask_user",
      "plan_review",
    ]);
    expect(visible.filter((v) => v.kind === "team_preview")).toHaveLength(0);

    renderResume();
    expect(screen.getByText("这次讨论怎么推进？")).toBeTruthy();
    expect(screen.getByText("「研」已完成")).toBeTruthy();
    expect(screen.queryByText("此回合还停在开工确认")).toBeNull();
    expect(screen.queryByText("最新开工卡")).toBeNull();
    expect(screen.queryByRole("button", { name: "授权并开工" })).toBeNull();
  });
});

describe("ResumePrompt · ask continue → leftover team_preview SSE skip", () => {
  const SERVER = "m-server-ask-tp";

  function seedAskPaused(): void {
    useConversationStore.setState({ currentConversationId: null, byId: {} });
    useInteractionStore.getState().clear();
    usePausedTurnStore.getState().clear();
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().setTurnPhase("streaming", CID);
    useConversationStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "怎么推进？",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    useConversationStore.getState().addMessage({
      id: "client-ask",
      role: "assistant",
      content: "先确认路径",
      createdAt: "",
      executionId: null,
      isStreaming: true,
    });
    useConversationStore
      .getState()
      .setServerMessageIdOnLastMessage(SERVER, CID);
    useInteractionStore.getState().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: SERVER,
      origin: "server",
      payload: {
        checkpoint_id: "cp-ask",
        conversation_id: CID,
        question: "这次讨论怎么推进？",
        assumptions: [],
        questions: [],
      },
    });
    useConversationStore.getState().finalizeLastMessage(CID);
    useInteractionStore.getState().markResolved({
      kind: "ask_user",
      id: "cp-ask",
      resolution: { decision: "continue" },
    });
  }

  it("leftover team_preview_required after continue is skipped (no IX)", () => {
    seedAskPaused();
    useConversationStore.getState().setTurnPhase("streaming", CID);
    expect(
      useConversationStore.getState().resumePausedAssistant(SERVER, CID),
    ).toBe("client-ask");

    handleInteractionEvent(leftoverPreviewRequired("tp-after-ask"), {
      conversationId: CID,
      source: "server",
    });

    expect(
      useInteractionStore.getState().byId.get("tp-after-ask"),
    ).toBeUndefined();
    expect(
      selectVisibleColdResumes({
        conversationId: CID,
        byId: useInteractionStore.getState().byId,
        pausedPending: usePausedTurnStore.getState().pending,
        messages: useConversationStore.getState().byId[CID]?.messages,
      }),
    ).toHaveLength(0);

    renderResume();
    expect(screen.queryByText("继续")).toBeNull();
    expect(screen.queryByText("此回合还停在开工确认")).toBeNull();
  });

  it("ensureStreamingAssistant reuses stamped paused bubble (no unstamped mint)", () => {
    seedAskPaused();
    useConversationStore.getState().setTurnPhase("streaming", CID);
    const before = useConversationStore
      .getState()
      .byId[CID]?.messages.filter((m) => m.role === "assistant").length;

    ensureStreamingAssistant(CID);

    const assistants = useConversationStore
      .getState()
      .byId[CID]?.messages.filter((m) => m.role === "assistant");
    expect(assistants).toHaveLength(before);
    expect(assistants[0].id).toBe("client-ask");
    expect(assistants[0].serverMessageId).toBe(SERVER);
    expect(assistants[0].isStreaming).toBe(true);

    handleInteractionEvent(leftoverPreviewRequired("tp-ensure"), {
      conversationId: CID,
      source: "server",
    });
    expect(
      useInteractionStore.getState().byId.get("tp-ensure"),
    ).toBeUndefined();
    renderResume();
    expect(screen.queryByText("继续")).toBeNull();
    expect(screen.queryByText("此回合还停在开工确认")).toBeNull();
  });

  it("message_start after continue keeps stamp; leftover team_preview does not paint", () => {
    seedAskPaused();
    useConversationStore.getState().setTurnPhase("streaming", CID);
    useConversationStore.getState().resumePausedAssistant(SERVER, CID);

    handleMessageStreamEvent(
      {
        type: "message_start",
        timestamp: "",
        payload: { message_id: SERVER, trace_id: "tr-cont" },
      },
      { conversationId: CID, source: "server" },
    );

    handleInteractionEvent(leftoverPreviewRequired("tp-after-start"), {
      conversationId: CID,
      source: "server",
    });

    const assistant = useConversationStore
      .getState()
      .byId[CID]?.messages.find((m) => m.role === "assistant");
    expect(assistant?.serverMessageId).toBe(SERVER);
    expect(
      useInteractionStore.getState().byId.get("tp-after-start"),
    ).toBeUndefined();

    renderResume();
    expect(screen.queryByText("继续")).toBeNull();
    expect(screen.queryByText("此回合还停在开工确认")).toBeNull();
  });

  it("unstamped window: no clickable card", () => {
    useConversationStore.setState({ currentConversationId: null, byId: {} });
    useInteractionStore.getState().clear();
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().setTurnPhase("streaming", CID);
    useConversationStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "组团",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    useConversationStore.getState().addMessage({
      id: "client-new",
      role: "assistant",
      content: "",
      createdAt: "",
      executionId: null,
      isStreaming: true,
    });

    handleInteractionEvent(leftoverPreviewRequired("tp-nostamp-window"), {
      conversationId: CID,
      source: "server",
    });

    expect(
      useInteractionStore.getState().byId.get("tp-nostamp-window"),
    ).toBeUndefined();
    expect(
      selectVisibleColdResumes({
        conversationId: CID,
        byId: useInteractionStore.getState().byId,
        pausedPending: [],
        messages: useConversationStore.getState().byId[CID]?.messages,
      }),
    ).toHaveLength(0);

    const { container } = renderResume();
    expect(container.querySelector(".mx-4")).toBeNull();
    expect(screen.queryByText("继续")).toBeNull();
  });

  it("resolved IX suppresses recovery shell (align mobile coldResume)", () => {
    useInteractionStore.getState().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: "m-server-tp",
      payload: { checkpoint_id: "cp-resolved-shell", question: "怎么推进？" },
    });
    useInteractionStore.getState().markResolved({
      kind: "ask_user",
      id: "cp-resolved-shell",
    });
    usePausedTurnStore.getState().addLiveResume({
      messageId: "m-server-tp",
      conversationId: CID,
      checkpointId: "cp-resolved-shell",
      kind: "ask_user",
      userMessage: "问",
      userMessageId: "u1",
      steps: [],
      pending: [],
      workers: [],
      tools: [],
      primitive: "delegate",
      motion: "",
      form: "",
      sides: [],
      maxRounds: 0,
      thorough: true,
      question: "怎么推进？",
      assumptions: [],
      questions: [],
      intent: "decision",
      origin: "server",
    });

    expect(
      selectVisibleColdResumes({
        conversationId: CID,
        byId: useInteractionStore.getState().byId,
        pausedPending: usePausedTurnStore.getState().pending,
        messages: useConversationStore.getState().byId[CID]?.messages ?? [],
      }),
    ).toHaveLength(0);
  });
});
