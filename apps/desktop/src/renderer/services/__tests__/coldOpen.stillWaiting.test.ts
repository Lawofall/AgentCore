/**
 * Cold open: clickable ask = still waiting (recovery pause frame / live origin),
 * not journal `checkpoint_required` alone.
 */
import { conversationHasPendingDecision } from "@/lib/composerPendingHint";
import { useConversationStore } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import { beforeEach, describe, expect, it } from "vitest";
import { selectVisibleColdResumes } from "../resume";

const CID = "conv-still-waiting";
const MID = "m-ask";
const CP = "cp-ask";

function seedAskMessages(): void {
  const conv = useConversationStore.getState();
  conv.switchConversation(CID);
  conv.addMessage(
    {
      id: "u1",
      role: "user",
      content: "选方案",
      createdAt: "2026-01-01T00:00:00Z",
      executionId: null,
      isStreaming: false,
    },
    CID,
  );
  conv.addMessage(
    {
      id: MID,
      role: "assistant",
      content: "请拍板",
      createdAt: "2026-01-01T00:00:01Z",
      executionId: null,
      isStreaming: false,
      serverMessageId: MID,
      runs: {
        events: [
          {
            type: "checkpoint_required",
            timestamp: "",
            payload: {
              checkpoint_id: CP,
              conversation_id: CID,
              question: "Canvas 还是 WebGL？",
              assumptions: [],
              questions: [],
            },
          },
        ],
        finishReason: "paused",
      },
    },
    CID,
  );
}

function hydrateJournalRequired(): void {
  useInteractionStore.getState().upsertRequired({
    kind: "ask_user",
    conversationId: CID,
    messageId: MID,
    payload: {
      checkpoint_id: CP,
      conversation_id: CID,
      question: "Canvas 还是 WebGL？",
      assumptions: [],
      questions: [],
    },
  });
}

function paint(recoveryState: "unresolved" | "ready" | "failed") {
  return selectVisibleColdResumes({
    conversationId: CID,
    byId: useInteractionStore.getState().byId,
    pausedPending: usePausedTurnStore.getState().pending,
    messages: useConversationStore.getState().byId[CID]?.messages ?? [],
    recoveryState,
  });
}

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useInteractionStore.getState().clear();
  usePausedTurnStore.getState().clear();
});

describe("cold open still-waiting", () => {
  it("journal required + recovery ready empty → no clickable card (claimed, cloud still hang snapshot)", () => {
    seedAskMessages();
    hydrateJournalRequired();
    usePausedTurnStore.getState().markOpenRecovery(CID, "ready");

    expect(paint("ready")).toHaveLength(0);
    expect(conversationHasPendingDecision(CID)).toBe(false);
  });

  it("journal required before recovery lands → no clickable card (no GET-first flash)", () => {
    seedAskMessages();
    hydrateJournalRequired();

    expect(paint("unresolved")).toHaveLength(0);
  });

  it("recovery still has the pause frame → clickable", () => {
    seedAskMessages();
    hydrateJournalRequired();
    usePausedTurnStore.getState().addLiveResume({
      messageId: MID,
      conversationId: CID,
      checkpointId: CP,
      kind: "ask_user",
      userMessage: "选方案",
      userMessageId: "u1",
      steps: [],
      pending: [],
      question: "Canvas 还是 WebGL？",
      assumptions: [],
      questions: [],
      intent: "decision",
      origin: "server",
    });
    usePausedTurnStore.getState().markOpenRecovery(CID, "ready");

    const cards = paint("ready");
    expect(cards).toHaveLength(1);
    expect(cards[0]?.checkpointId).toBe(CP);
  });

  it("live origin paints without a pause frame", () => {
    seedAskMessages();
    useInteractionStore.getState().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: MID,
      origin: "server",
      payload: {
        checkpoint_id: CP,
        conversation_id: CID,
        question: "Canvas 还是 WebGL？",
        assumptions: [],
        questions: [],
      },
    });

    expect(paint("unresolved")).toHaveLength(1);
    expect(paint("ready")).toHaveLength(1);
  });

  it("recovery failed falls back to journal pending (unknown ≠ idle)", () => {
    seedAskMessages();
    hydrateJournalRequired();
    usePausedTurnStore.getState().markOpenRecovery(CID, "failed");

    expect(paint("failed")).toHaveLength(1);
  });

  it("journal checkpoint_resolved vetoes recovery-failed fallback", () => {
    seedAskMessages();
    hydrateJournalRequired();
    const ast = useConversationStore
      .getState()
      .byId[CID]?.messages.find((m) => m.id === MID);
    if (!ast?.runs?.events) throw new Error("seed missing journal");
    ast.runs.events = [
      ...ast.runs.events,
      {
        type: "checkpoint_resolved",
        timestamp: "",
        payload: { checkpoint_id: CP, decision: "continue" },
      },
    ];

    expect(paint("failed")).toHaveLength(0);
  });

  it("IX resolved vetoes a matching pause frame", () => {
    seedAskMessages();
    hydrateJournalRequired();
    useInteractionStore.getState().markResolved({
      kind: "ask_user",
      id: CP,
      resolution: { decision: "continue" },
    });
    usePausedTurnStore.getState().addLiveResume({
      messageId: MID,
      conversationId: CID,
      checkpointId: CP,
      kind: "ask_user",
      userMessage: "选方案",
      userMessageId: "u1",
      steps: [],
      pending: [],
      question: "Canvas 还是 WebGL？",
      assumptions: [],
      questions: [],
      intent: "decision",
      origin: "server",
    });
    usePausedTurnStore.getState().markOpenRecovery(CID, "ready");

    expect(paint("ready")).toHaveLength(0);
  });
});
