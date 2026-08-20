import { useConversationStore } from "@/stores/conversation";
import {
  hydrateInteractionsFromJournal,
  useInteractionStore,
} from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import type {
  CheckpointRequiredPayload,
  PlanReviewRequiredPayload,
} from "@/types/events";
import { beforeEach, describe, expect, it } from "vitest";
import { toMessage } from "../messages";
import {
  conversationHasColdPending,
  isClientOnlyResumeKey,
  listVisibleColdResumes,
  resolveResumeMessageId,
  resolveResumeOrigin,
  surfaceResumeFromLiveTurn,
} from "../resume";

// 挂起即收口 (②) cold-path coverage: a turn that ENDS at a checkpoint on the live stream
// (message_end finish_reason=paused) must hand off to the SINGLE durable resume card,
// keyed by the SERVER message_id (the bubble's own id is a client UUID that would 404 the
// frame). These exercise that surfacing in isolation — the unit the live message_end
// handler calls — which had no renderer test before the flag rollout.

const conv = () => useConversationStore.getState();
const paused = () => usePausedTurnStore.getState();
const ix = () => useInteractionStore.getState();

const CID = "conv-1";

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  usePausedTurnStore.getState().clear();
  useInteractionStore.getState().clear();
});

/** Seed a user request + a (streaming) assistant bubble whose client id is a UUID,
 * optionally stamping the authoritative server message_id (as message_start would). */
function seedTurn(serverMessageId?: string): void {
  conv().switchConversation(CID);
  conv().addMessage({
    id: "u1",
    role: "user",
    content: "做 A 还是 B？",
    createdAt: "",
    executionId: null,
    isStreaming: false,
  });
  conv().addMessage({
    id: "client-uuid",
    role: "assistant",
    content: "",
    createdAt: "",
    executionId: null,
    isStreaming: true,
  });
  if (serverMessageId)
    conv().setServerMessageIdOnLastMessage(serverMessageId, CID);
}

const cpPayload = (
  over: Partial<CheckpointRequiredPayload> = {},
): CheckpointRequiredPayload => ({
  checkpoint_id: "cp1",
  conversation_id: CID,
  question: "先做 A 还是 B?",
  assumptions: [],
  questions: [],
  ...over,
});

const prPayload = (
  over: Partial<PlanReviewRequiredPayload> = {},
): PlanReviewRequiredPayload => ({
  checkpoint_id: "pr1",
  conversation_id: CID,
  steps: [{ run_id: "r1", role: "调研", summary: "方案就绪" }],
  pending: [{ run_id: "r2", role: "执行" }],
  ...over,
});

function upsertAsk(messageId = "client-uuid"): void {
  ix().upsertRequired({
    kind: "ask_user",
    conversationId: CID,
    messageId,
    payload: cpPayload() as unknown as Record<string, unknown>,
  });
}

function upsertPlanReview(messageId = "client-uuid"): void {
  ix().upsertRequired({
    kind: "plan_review",
    conversationId: CID,
    messageId,
    payload: prPayload() as unknown as Record<string, unknown>,
  });
}

function upsertTeamPreview(messageId = "client-uuid"): void {
  ix().upsertRequired({
    kind: "team_preview",
    conversationId: CID,
    messageId,
    payload: {
      checkpoint_id: "tp1",
      conversation_id: CID,
      primitive: "delegate",
      workers: [{ run_id: "r1", role: "调研", task: "做调研", depends_on: [] }],
      tools: ["file_write"],
      motion: "",
      form: "",
      sides: [],
      max_rounds: 0,
      thorough: true,
    },
  });
}

describe("surfaceResumeFromLiveTurn", () => {
  it("surfaces one ask_user resume entry keyed by the SERVER message_id", () => {
    seedTurn("m-server-1");
    upsertAsk();

    surfaceResumeFromLiveTurn(CID, "server");

    const entries = paused().pending;
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      // the resume KEY is the server id, NOT the client UUID bubble id (which 404s)
      messageId: "m-server-1",
      conversationId: CID,
      checkpointId: "cp1",
      kind: "ask_user",
      question: "先做 A 还是 B?",
      userMessage: "做 A 还是 B？",
      userMessageId: "u1",
    });
  });

  it("surfaces one plan_review resume entry carrying steps + pending", () => {
    seedTurn("m-server-2");
    upsertPlanReview();

    surfaceResumeFromLiveTurn(CID, "server");

    const entries = paused().pending;
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      messageId: "m-server-2",
      checkpointId: "pr1",
      kind: "plan_review",
    });
    expect(entries[0].steps).toEqual([
      { run_id: "r1", role: "调研", summary: "方案就绪" },
    ]);
    expect(entries[0].pending).toEqual([{ run_id: "r2", role: "执行" }]);
    // 无 ceo_review 的 payload → 不合成空摘要。
    expect(entries[0].ceoReview).toBeUndefined();
  });

  it("plan_review 带 ceo_review 时透传把关摘要到 resume 帧", () => {
    seedTurn("m-server-cr");
    ix().upsertRequired({
      kind: "plan_review",
      conversationId: CID,
      messageId: "client-uuid",
      payload: prPayload({
        ceo_review: {
          conclusion: "可放行",
          risks: ["回滚预案缺失"],
          suggestions: ["先灰度"],
        },
      }) as unknown as Record<string, unknown>,
    });

    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending[0]?.ceoReview).toEqual({
      conclusion: "可放行",
      risks: ["回滚预案缺失"],
      suggestions: ["先灰度"],
    });
  });

  it("surfaces team_preview with intent=kickoff (not decision)", () => {
    seedTurn("m-server-tp");
    upsertTeamPreview();

    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending).toHaveLength(1);
    expect(paused().pending[0]).toMatchObject({
      messageId: "m-server-tp",
      checkpointId: "tp1",
      kind: "team_preview",
      intent: "kickoff",
    });
  });

  it("画卡后清 isGenerating / isStreaming（冷挂起不变量）", () => {
    seedTurn("m-server-gen");
    conv().setGenerating(true, CID);
    expect(conv().byId[CID]?.isGenerating).toBe(true);
    expect(
      conv().byId[CID]?.messages.find((m) => m.id === "client-uuid")
        ?.isStreaming,
    ).toBe(true);
    upsertAsk();

    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending).toHaveLength(1);
    expect(conv().byId[CID]?.isGenerating).toBe(false);
    const assistant = conv().byId[CID]?.messages.find(
      (m) => m.id === "client-uuid",
    );
    expect(assistant?.isStreaming).toBe(false);
    expect(assistant?.finishReason).toBe("paused");
  });

  it("does not paint a resume card when no server id was stamped", () => {
    // Without a stamp the durable frame key is unknown — never show a clickable
    // card keyed by the client bubble id (would trip the client-only resume guard).
    seedTurn(); // client-uuid, no serverMessageId
    upsertAsk();

    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending).toHaveLength(0);
  });

  it("is idempotent by messageId — a second call does not stack a duplicate", () => {
    seedTurn("m-server-1");
    upsertAsk();

    surfaceResumeFromLiveTurn(CID, "server");
    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending).toHaveLength(1);
  });

  it("is a no-op when the finalized turn carries no pending checkpoint", () => {
    seedTurn("m-server-1");

    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending).toHaveLength(0);
  });

  it("does nothing when the conversation has no assistant turn", () => {
    conv().switchConversation(CID); // empty slice

    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending).toHaveLength(0);
  });

  it("tags origin=sidecar when caller passes sidecar", () => {
    seedTurn("m-server-1");
    upsertAsk();

    surfaceResumeFromLiveTurn(CID, "sidecar");

    expect(paused().pending[0]?.origin).toBe("sidecar");
  });

  it("tags origin=server when caller passes server", () => {
    seedTurn("m-server-1");
    upsertAsk();

    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending[0]?.origin).toBe("server");
  });

  it("does not clobber existing sidecar origin when caller passes server", () => {
    seedTurn("m-server-1");
    upsertAsk();
    surfaceResumeFromLiveTurn(CID, "sidecar");
    expect(paused().pending[0]?.origin).toBe("sidecar");

    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending[0]?.origin).toBe("sidecar");
  });

  it("prefers InteractionStore sidecar origin over caller server", () => {
    seedTurn("m-server-1");
    ix().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: "m-server-1",
      origin: "sidecar",
      payload: cpPayload() as unknown as Record<string, unknown>,
    });

    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending[0]?.origin).toBe("sidecar");
  });
});

describe("listVisibleColdResumes (InteractionStore authority)", () => {
  it("paints from IX cold pending without pausedTurns surface", () => {
    seedTurn("m-server-tp");
    ix().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "m-server-tp",
      origin: "server",
      payload: {
        checkpoint_id: "tp1",
        conversation_id: CID,
        primitive: "delegate",
        workers: [
          { run_id: "r1", role: "调研", task: "做调研", depends_on: [] },
        ],
        tools: ["file_write"],
        motion: "",
        form: "",
        sides: [],
        max_rounds: 0,
        thorough: true,
      },
    });

    const visible = listVisibleColdResumes(CID);
    expect(visible).toHaveLength(1);
    expect(visible[0]).toMatchObject({
      messageId: "m-server-tp",
      checkpointId: "tp1",
      kind: "team_preview",
      origin: "server",
    });
    expect(paused().pending).toHaveLength(0);
  });

  it("second-round ask paints after prior ask resolved (new checkpoint id)", () => {
    seedTurn("m-server-1");
    upsertAsk("m-server-1");
    ix().markResolved({
      kind: "ask_user",
      id: "cp1",
      resolution: { decision: "continue" },
    });
    expect(listVisibleColdResumes(CID)).toHaveLength(0);

    conv().addMessage({
      id: "u2",
      role: "user",
      content: "再问",
      createdAt: "",
      executionId: null,
      isStreaming: false,
    });
    conv().addMessage({
      id: "client-r2",
      role: "assistant",
      content: "",
      createdAt: "",
      executionId: null,
      isStreaming: true,
    });
    conv().setServerMessageIdOnLastMessage("m-server-2", CID);
    ix().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: "m-server-2",
      payload: cpPayload({
        checkpoint_id: "cp2",
        question: "第二轮？",
      }) as unknown as Record<string, unknown>,
    });

    const visible = listVisibleColdResumes(CID);
    expect(visible).toHaveLength(1);
    expect(visible[0]).toMatchObject({
      messageId: "m-server-2",
      checkpointId: "cp2",
      kind: "ask_user",
      question: "第二轮？",
    });
  });

  it("stamp from none → paints once serverMessageId lands", () => {
    seedTurn(); // client-uuid, no stamp
    upsertTeamPreview("client-uuid");
    expect(listVisibleColdResumes(CID)).toHaveLength(0);

    conv().setServerMessageIdOnLastMessage("m-server-stamp", CID);
    const visible = listVisibleColdResumes(CID);
    expect(visible).toHaveLength(1);
    expect(visible[0]?.messageId).toBe("m-server-stamp");
  });

  it("prefers Interaction origin over pausedTurns shell", () => {
    seedTurn("m-server-1");
    ix().upsertRequired({
      kind: "ask_user",
      conversationId: CID,
      messageId: "m-server-1",
      origin: "sidecar",
      payload: cpPayload() as unknown as Record<string, unknown>,
    });
    expect(resolveResumeOrigin(CID, "m-server-1")).toBe("sidecar");
    expect(conversationHasColdPending(CID)).toBe(true);
  });
});

describe("cold checkpoint terminal authority", () => {
  function stampJournalResolved(
    checkpointId: string,
    type:
      | "team_preview_resolved"
      | "checkpoint_resolved" = "team_preview_resolved",
  ): void {
    const assistant = [...getMessages()]
      .reverse()
      .find((m) => m.role === "assistant");
    if (!assistant) throw new Error("expected assistant");
    conv().updateMessage(
      assistant.id,
      {
        runs: {
          events: [
            {
              type,
              timestamp: "",
              payload: { checkpoint_id: checkpointId },
            },
          ],
          finishReason: "stop",
        },
      },
      CID,
    );
  }

  function getMessages() {
    return conv().byId[CID]?.messages ?? [];
  }

  it("POST drop reopen keeps submitting when journal already has *_resolved", () => {
    seedTurn("m-server-tp");
    upsertTeamPreview("m-server-tp");
    expect(ix().beginSubmit("tp1")).toBe(true);
    stampJournalResolved("tp1");

    ix().reopen("tp1");

    expect(ix().get("tp1")?.status).toBe("submitting");
    expect(listVisibleColdResumes(CID)).toHaveLength(0);
    expect(conversationHasColdPending(CID)).toBe(false);
  });

  it("attach replay of required after journal resolved does not paint a clickable card", () => {
    seedTurn("5e78ddbf-turn");
    upsertTeamPreview("5e78ddbf-turn");
    expect(ix().beginSubmit("tp1")).toBe(true);
    stampJournalResolved("tp1");
    ix().reopen("tp1");
    expect(ix().get("tp1")?.status).toBe("submitting");

    ix().upsertRequired({
      kind: "team_preview",
      conversationId: CID,
      messageId: "5e78ddbf-turn",
      payload: {
        checkpoint_id: "tp1",
        conversation_id: CID,
        primitive: "delegate",
        workers: [
          { run_id: "r1", role: "调研", task: "做调研", depends_on: [] },
        ],
        tools: ["file_write"],
        motion: "",
        form: "",
        sides: [],
        max_rounds: 0,
        thorough: true,
      },
    });
    surfaceResumeFromLiveTurn(CID, "server");

    expect(listVisibleColdResumes(CID)).toHaveLength(0);
  });

  it("message_end(paused) shell is not clickable when journal has *_resolved", () => {
    seedTurn("m-server-tp");
    upsertTeamPreview("m-server-tp");
    stampJournalResolved("tp1");
    surfaceResumeFromLiveTurn(CID, "server");

    expect(paused().pending.length).toBeGreaterThan(0);
    expect(listVisibleColdResumes(CID)).toHaveLength(0);
  });

  it("switch-back journal hydrate of required→resolved does not paint", () => {
    seedTurn("m-hydrated");
    hydrateInteractionsFromJournal(CID, "m-hydrated", [
      {
        type: "team_preview_required",
        payload: {
          checkpoint_id: "tp-hy",
          conversation_id: CID,
          primitive: "delegate",
          workers: [],
        },
      },
      {
        type: "team_preview_resolved",
        payload: { checkpoint_id: "tp-hy", decision: "continue" },
      },
    ]);
    stampJournalResolved("tp-hy");
    surfaceResumeFromLiveTurn(CID, "server");

    expect(ix().get("tp-hy")?.status).toBe("resolved");
    expect(listVisibleColdResumes(CID)).toHaveLength(0);
  });
});

describe("isClientOnlyResumeKey", () => {
  it("is true for a live client bubble that never got message_start", () => {
    seedTurn(); // client-uuid, no serverMessageId stamp
    expect(isClientOnlyResumeKey(CID, "client-uuid")).toBe(true);
  });

  it("is false after live message_start stamps serverMessageId", () => {
    seedTurn("m-server-1");
    // Resume key is the SERVER id; looking up by client id finds the bubble
    // but serverMessageId is set → not client-only.
    expect(isClientOnlyResumeKey(CID, "client-uuid")).toBe(false);
  });

  it("is false for a hydrated assistant (toMessage stamps serverMessageId)", () => {
    conv().switchConversation(CID);
    conv().addMessage(
      toMessage({
        id: "srv-msg-1",
        conversation_id: CID,
        role: "assistant",
        content: "paused reply",
        reasoning_content: null,
        created_at: "2026-01-01T00:00:00Z",
      }),
    );

    // After reload, bubble id === server id; guard must not reject resume.
    expect(isClientOnlyResumeKey(CID, "srv-msg-1")).toBe(false);
  });
});

describe("resolveResumeMessageId", () => {
  it("rekeys a pending card from client bubble id to stamped server id", () => {
    seedTurn("m-server-1");
    paused().addLiveResume({
      messageId: "client-uuid",
      conversationId: CID,
      checkpointId: "cp1",
      kind: "ask_user",
      userMessage: "做 A 还是 B？",
      userMessageId: "u1",
      origin: "server",
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
      question: "先做 A 还是 B?",
      assumptions: [],
      questions: [],
      intent: "decision",
    });

    expect(resolveResumeMessageId(CID, "client-uuid")).toBe("m-server-1");
    expect(paused().pending).toHaveLength(1);
    expect(paused().pending[0]?.messageId).toBe("m-server-1");
  });

  it("returns the input key unchanged when the bubble has no stamp", () => {
    seedTurn();
    expect(resolveResumeMessageId(CID, "client-uuid")).toBe("client-uuid");
  });

  it("is a no-op when already keyed by the server id", () => {
    seedTurn("m-server-1");
    expect(resolveResumeMessageId(CID, "m-server-1")).toBe("m-server-1");
  });
});
