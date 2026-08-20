import { useConversationStore } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  COMPOSER_PENDING_HINT,
  COMPOSER_PENDING_SEND_CONFIRM,
  ackSendDespitePending,
  confirmSendDespitePendingIfNeeded,
  conversationHasPendingDecision,
  resetSendDespitePendingAcks,
  shouldConfirmSendDespitePending,
} from "../composerPendingHint";

const CID = "conv_pending_hint";

beforeEach(() => {
  resetSendDespitePendingAcks();
  usePausedTurnStore.getState().clear();
  useInteractionStore.getState().clear();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  vi.restoreAllMocks();
});

describe("composerPendingHint", () => {
  it("exposes short zh copy", () => {
    expect(COMPOSER_PENDING_HINT).toContain("待你确认");
    expect(COMPOSER_PENDING_HINT).toContain("另开一轮");
    expect(COMPOSER_PENDING_HINT).toContain("确认卡仍保留");
    expect(COMPOSER_PENDING_HINT).not.toContain("取消等待");
    expect(COMPOSER_PENDING_SEND_CONFIRM).toContain("另开一轮");
    expect(COMPOSER_PENDING_SEND_CONFIRM).toContain("确认卡仍保留");
    expect(COMPOSER_PENDING_SEND_CONFIRM).not.toContain("取消等待");
    expect(COMPOSER_PENDING_SEND_CONFIRM).toContain("确定继续");
  });

  it("detects pausedTurns for the conversation", () => {
    expect(conversationHasPendingDecision(CID)).toBe(false);
    usePausedTurnStore.getState().addLiveResume({
      messageId: "m1",
      conversationId: CID,
      checkpointId: "cp1",
      kind: "team_preview",
      userMessage: "开工",
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
      question: "",
      context: "",
      assumptions: [],
      questions: [],
      intent: "kickoff",
      origin: "server",
    });
    expect(conversationHasPendingDecision(CID)).toBe(true);
    expect(conversationHasPendingDecision("other")).toBe(false);
  });

  it("detects cold InteractionStore pending without pausedTurns", () => {
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
      id: "client-a",
      role: "assistant",
      content: "",
      createdAt: "",
      executionId: null,
      isStreaming: true,
      serverMessageId: "m1",
    });
    useInteractionStore.getState().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "m1",
      origin: "server",
      payload: {
        checkpoint_id: "tp-hint",
        conversation_id: CID,
        primitive: "delegate",
        workers: [],
        tools: [],
      },
    });
    expect(conversationHasPendingDecision(CID)).toBe(true);
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
  });

  it("ignores cold pending until assistant has a server stamp", () => {
    useConversationStore.getState().switchConversation(CID);
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
      origin: "server",
      payload: {
        checkpoint_id: "tp-nostamp-hint",
        conversation_id: CID,
        primitive: "delegate",
        workers: [],
        tools: [],
      },
    });
    expect(conversationHasPendingDecision(CID)).toBe(false);
  });

  it("detects pending approval interactions", () => {
    useInteractionStore.getState().hydratePending(CID, [
      {
        kind: "approval",
        id: "a1",
        messageId: "m1",
        payload: { approval_id: "a1", tool_name: "bash" },
      },
    ]);
    expect(conversationHasPendingDecision(CID)).toBe(true);
  });

  it("hydratePending terminal stub is not a pending decision", () => {
    useInteractionStore.getState().upsertRequired({
      kind: "approval",
      conversationId: CID,
      messageId: "m1",
      origin: "server",
      payload: { approval_id: "a-stub", tool_name: "bash", arguments: {} },
    });
    expect(conversationHasPendingDecision(CID)).toBe(true);
    useInteractionStore.getState().hydratePending(CID, [], {
      confirmed: ["server"],
    });
    expect(conversationHasPendingDecision(CID)).toBe(false);
  });

  it("session ack suppresses further confirms", () => {
    usePausedTurnStore.getState().addLiveResume({
      messageId: "m1",
      conversationId: CID,
      checkpointId: "cp1",
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
      question: "q",
      context: "",
      assumptions: [],
      questions: [],
      intent: "decision",
      origin: "server",
    });
    expect(shouldConfirmSendDespitePending(CID)).toBe(true);
    ackSendDespitePending(CID);
    expect(shouldConfirmSendDespitePending(CID)).toBe(false);
  });

  it("confirmSendDespitePendingIfNeeded: skip while generating; confirm once", () => {
    usePausedTurnStore.getState().addLiveResume({
      messageId: "m1",
      conversationId: CID,
      checkpointId: "cp1",
      kind: "plan_review",
      userMessage: "计划",
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
      question: "",
      context: "",
      assumptions: [],
      questions: [],
      intent: "kickoff",
      origin: "server",
    });

    expect(confirmSendDespitePendingIfNeeded(CID, true)).toBe(true);

    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    expect(confirmSendDespitePendingIfNeeded(CID, false)).toBe(false);
    expect(confirm).toHaveBeenCalledWith(COMPOSER_PENDING_SEND_CONFIRM);

    confirm.mockReturnValue(true);
    expect(confirmSendDespitePendingIfNeeded(CID, false)).toBe(true);
    expect(confirmSendDespitePendingIfNeeded(CID, false)).toBe(true);
    expect(confirm).toHaveBeenCalledTimes(2);
  });
});
