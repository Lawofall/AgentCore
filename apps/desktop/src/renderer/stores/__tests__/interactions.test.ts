import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  type InteractionEntry,
  applyInteractionWireEvent,
  entryToCheckpoint,
  entryToPlanReview,
  hydrateInteractionsFromJournal,
  isAwaitingUserEntry,
  isHotGateInteractionKind,
  messageCheckpoints,
  messagePlanReviews,
  noteColdServerSettled,
  useInteractionStore,
} from "../interactions";
import { beginPausedSnapshot } from "../pausedTurns";

const store = () => useInteractionStore.getState();

beforeEach(() => {
  store().clear();
});

describe("InteractionStore", () => {
  it("upserts required payloads for all hot/cold kinds", () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: {
        approval_id: "a1",
        tool_name: "file_write",
        arguments: {},
      },
    });
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "cp1", question: "继续吗？" },
    });
    expect(store().get("a1")?.status).toBe("pending");
    expect(store().get("cp1")?.kind).toBe("ask_user");
    expect(store().listPending("c1")).toHaveLength(2);
  });

  it("ignores duplicate required for an already-resolved id", () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: { approval_id: "a1", tool_name: "x", arguments: {} },
    });
    store().markResolved({ kind: "approval", id: "a1" });
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: { approval_id: "a1", tool_name: "y", arguments: {} },
    });
    expect(store().get("a1")?.status).toBe("resolved");
    expect(
      (store().get("a1")?.payload as { tool_name?: string }).tool_name,
    ).toBe("x");
  });

  it("resolved stub (resolved-before-required) yields to a live required payload", () => {
    store().markResolved({
      kind: "ask_user",
      id: "cp-stub",
      resolution: { decision: "continue" },
    });
    expect(store().get("cp-stub")?.status).toBe("resolved");
    expect(store().get("cp-stub")?.payload).toEqual({});

    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m2",
      payload: { checkpoint_id: "cp-stub", question: "还要拍板吗？" },
    });
    expect(store().get("cp-stub")?.status).toBe("pending");
    expect(store().get("cp-stub")?.messageId).toBe("m2");
    expect(
      (store().get("cp-stub")?.payload as { question?: string }).question,
    ).toBe("还要拍板吗？");
  });

  it("cold required on a new host messageId replaces a prior resolved entry", () => {
    store().upsertRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "m-turn1",
      payload: {
        checkpoint_id: "pr-reuse",
        steps: [],
        pending: [],
      },
    });
    store().markResolved({
      kind: "plan_review",
      id: "pr-reuse",
      resolution: { decision: "continue" },
    });
    store().upsertRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "m-turn2",
      payload: {
        checkpoint_id: "pr-reuse",
        steps: [{ run_id: "r2", role: "研", summary: "t" }],
        pending: [],
      },
    });
    expect(store().get("pr-reuse")?.status).toBe("pending");
    expect(store().get("pr-reuse")?.messageId).toBe("m-turn2");
  });

  it("status:pending force replaces a resolved cold entry (recovery)", () => {
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "cp-force", question: "旧" },
    });
    store().markResolved({ kind: "ask_user", id: "cp-force" });
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      status: "pending",
      payload: { checkpoint_id: "cp-force", question: "恢复" },
    });
    expect(store().get("cp-force")?.status).toBe("pending");
    expect(
      (store().get("cp-force")?.payload as { question?: string }).question,
    ).toBe("恢复");
  });

  it("orphaned-before-required builds terminal stub; required cannot resurrect pending", () => {
    applyInteractionWireEvent(
      "interaction_orphaned",
      { interaction_id: "sc1", kind: "stage_card" },
      "c1",
      "m1",
    );
    expect(store().get("sc1")?.status).toBe("orphaned");
    expect(store().get("sc1")?.kind).toBe("stage_card");
    applyInteractionWireEvent(
      "stage_card_required",
      {
        stage_card_id: "sc1",
        motion: "是否开辩",
        sides: [],
        form: "debate",
      },
      "c1",
      "m1",
    );
    expect(store().get("sc1")?.status).toBe("orphaned");
  });

  it("beginSubmit / reopen / markOrphaned lifecycle", () => {
    store().upsertRequired({
      kind: "escalation",
      conversationId: "c1",
      messageId: "m1",
      payload: { escalation_id: "e1", question: "q", assumption: "a" },
    });
    expect(store().beginSubmit("e1")).toBe(true);
    expect(store().get("e1")?.status).toBe("submitting");
    expect(store().beginSubmit("e1")).toBe(false);
    store().reopen("e1");
    expect(store().get("e1")?.status).toBe("pending");
    store().markOrphaned("e1");
    expect(store().get("e1")?.status).toBe("orphaned");
  });

  it("reopen does not flip a server-settled cold card back to pending", () => {
    store().upsertRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "m1",
      payload: {
        checkpoint_id: "pr-settled",
        steps: [],
        pending: [],
      },
    });
    expect(store().beginSubmit("pr-settled")).toBe(true);
    noteColdServerSettled("pr-settled");
    store().reopen("pr-settled");
    expect(store().get("pr-settled")?.status).toBe("submitting");
  });

  it("reopen still returns an unsettled cold card to pending", () => {
    store().upsertRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "m1",
      payload: {
        checkpoint_id: "pr-live",
        steps: [],
        pending: [],
      },
    });
    expect(store().beginSubmit("pr-live")).toBe(true);
    store().reopen("pr-live");
    expect(store().get("pr-live")?.status).toBe("pending");
  });

  it("orphanConversation flips only hot pending cards", () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: { approval_id: "a1", tool_name: "x", arguments: {} },
    });
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "cp1", question: "q" },
    });
    store().upsertRequired({
      kind: "approval",
      conversationId: "c2",
      messageId: "m2",
      payload: { approval_id: "a2", tool_name: "x", arguments: {} },
    });
    store().orphanConversation("c1", true);
    expect(store().get("a1")?.status).toBe("orphaned");
    expect(store().get("cp1")?.status).toBe("pending");
    expect(store().get("a2")?.status).toBe("pending");
  });

  it("hydratePending replaces pending set for a conversation", () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { approval_id: "old", tool_name: "x", arguments: {} },
    });
    store().hydratePending(
      "c1",
      [
        {
          kind: "escalation",
          id: "d1",
          messageId: "m9",
          origin: "server",
          payload: {
            escalation_id: "d1",
            run_id: "r1",
            agent_id: "a1",
            question: "q?",
            assumption: "assume",
          },
        },
      ],
      { confirmed: ["server"] },
    );
    expect(store().get("old")?.status).toBe("resolved");
    expect(store().get("old")?.settledElsewhere).toBe(true);
    expect(store().get("d1")?.status).toBe("pending");
    expect(store().get("d1")?.kind).toBe("escalation");
  });

  it("empty hydratePending does not dispose sidecar / missing origin", () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      origin: "sidecar",
      payload: {
        approval_id: "a-side",
        tool_name: "host_shell",
        arguments: {},
      },
    });
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: {
        approval_id: "a-none",
        tool_name: "host_shell",
        arguments: {},
      },
    });
    store().hydratePending("c1", [], { confirmed: ["server"] });
    expect(store().get("a-side")?.status).toBe("pending");
    expect(store().get("a-none")?.status).toBe("pending");
  });

  it("empty hydratePending settles confirmed server-origin hot cards", () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { approval_id: "a1", tool_name: "file_write", arguments: {} },
    });
    store().hydratePending("c1", [], { confirmed: ["server"] });
    expect(store().get("a1")?.status).toBe("resolved");
    expect(store().get("a1")?.settledElsewhere).toBe(true);
    expect(store().listPending("c1")).toHaveLength(0);
  });

  it("empty hydratePending keeps cards that surfaced after since", () => {
    const since = beginPausedSnapshot();
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { approval_id: "a-live", tool_name: "x", arguments: {} },
    });
    store().hydratePending("c1", [], { since, confirmed: ["server"] });
    expect(store().get("a-live")?.status).toBe("pending");
  });

  it("local sidecar-origin hot empty only when sidecar asked and idle", () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      origin: "sidecar",
      payload: { approval_id: "a-side", tool_name: "x", arguments: {} },
    });
    store().hydratePending("c1", [], {
      confirmed: ["sidecar", "server"],
      sidecarLive: true,
    });
    expect(store().get("a-side")?.status).toBe("pending");
    store().hydratePending("c1", [], {
      confirmed: ["sidecar", "server"],
      sidecarLive: false,
    });
    expect(store().get("a-side")?.status).toBe("orphaned");
  });

  it("empty hydratePending never Map.delete cold kinds", () => {
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { checkpoint_id: "cp1", question: "继续吗？" },
    });
    store().upsertRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: {
        checkpoint_id: "pr1",
        steps: [],
        pending: [],
      },
    });
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { approval_id: "a1", tool_name: "x", arguments: {} },
    });
    store().hydratePending("c1", [], { confirmed: ["server"] });
    expect(store().get("cp1")?.status).toBe("pending");
    expect(store().get("pr1")?.status).toBe("pending");
    expect(store().get("a1")?.status).toBe("resolved");
  });

  it("non-empty hydratePending replaces confirmed server-origin even while sidecar live", () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { approval_id: "old", tool_name: "x", arguments: {} },
    });
    store().hydratePending(
      "c1",
      [
        {
          kind: "approval",
          id: "new",
          messageId: "m1",
          origin: "server",
          payload: { approval_id: "new", tool_name: "y", arguments: {} },
        },
      ],
      { confirmed: ["server"], sidecarLive: true },
    );
    expect(store().get("old")?.status).toBe("resolved");
    expect(store().get("old")?.settledElsewhere).toBe(true);
    expect(store().get("new")?.status).toBe("pending");
  });

  it("empty hydratePending leaves terminal stubs that are not pending", () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { approval_id: "a1", tool_name: "x", arguments: {} },
    });
    store().upsertRequired({
      kind: "escalation",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { escalation_id: "e1", question: "q", assumption: "a" },
    });
    store().upsertRequired({
      kind: "stage_card",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: {
        stage_card_id: "sc1",
        motion: "是否开辩",
        sides: [],
        form: "debate",
      },
    });
    store().hydratePending("c1", [], { confirmed: ["server"] });
    for (const id of ["a1", "e1", "sc1"] as const) {
      const entry = store().get(id);
      expect(entry?.status).toBe("resolved");
      expect(entry?.settledElsewhere).toBe(true);
      expect(entry && isAwaitingUserEntry(entry)).toBe(false);
    }
    expect(store().listPending("c1")).toHaveLength(0);
    const hotGatePending = [...store().byId.values()].filter(
      (e) =>
        e.conversationId === "c1" &&
        (e.status === "pending" || e.status === "submitting") &&
        isHotGateInteractionKind(e.kind),
    );
    expect(hotGatePending).toHaveLength(0);
  });

  it("settleUnseenCold marks terminal in place (no Map.delete)", () => {
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { checkpoint_id: "cp-gone", question: "q" },
    });
    store().upsertRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "m2",
      origin: "sidecar",
      payload: {
        checkpoint_id: "pr-gone",
        steps: [],
        pending: [],
      },
    });
    store().settleUnseenCold("c1", new Set(), {
      confirmed: ["server", "sidecar"],
    });
    expect(store().get("cp-gone")?.status).toBe("resolved");
    expect(store().get("cp-gone")?.resumeSettled).toEqual({
      decision: "",
      decidedAt: "",
      turnStatus: "unknown",
    });
    expect(store().get("pr-gone")?.status).toBe("orphaned");
  });

  it("settleUnseenCold is per-card (not gated on whole paused=[])", () => {
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { checkpoint_id: "cp-keep", question: "还在" },
    });
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m2",
      origin: "server",
      payload: { checkpoint_id: "cp-gone", question: "没了" },
    });
    store().settleUnseenCold("c1", new Set(["cp-keep"]), {
      confirmed: ["server"],
    });
    expect(store().get("cp-keep")?.status).toBe("pending");
    expect(store().get("cp-gone")?.status).toBe("resolved");
  });

  it("settleUnseenCold keeps cards after since or unconfirmed origin", () => {
    const since = beginPausedSnapshot();
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      origin: "server",
      payload: { checkpoint_id: "cp-new", question: "抢跑" },
    });
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m2",
      origin: "sidecar",
      payload: { checkpoint_id: "cp-side", question: "本机" },
    });
    store().settleUnseenCold("c1", new Set(), {
      since,
      confirmed: ["server"],
    });
    expect(store().get("cp-new")?.status).toBe("pending");
    expect(store().get("cp-side")?.status).toBe("pending");
  });

  it("applyInteractionWireEvent handles orphaned + required + resolved", () => {
    applyInteractionWireEvent(
      "approval_required",
      {
        approval_id: "a1",
        conversation_id: "c1",
        tool_call_id: "t1",
        tool_name: "file_write",
        arguments: {},
      },
      "c1",
      "m1",
    );
    expect(store().get("a1")?.status).toBe("pending");
    applyInteractionWireEvent(
      "approval_resolved",
      { approval_id: "a1", decision: "approve" },
      "c1",
      "m1",
    );
    expect(store().get("a1")?.status).toBe("resolved");

    applyInteractionWireEvent(
      "escalation_required",
      { escalation_id: "e1", question: "q", assumption: "a" },
      "c1",
      "m1",
    );
    applyInteractionWireEvent(
      "interaction_orphaned",
      { interaction_id: "e1", kind: "escalation" },
      "c1",
      "m1",
    );
    expect(store().get("e1")?.status).toBe("orphaned");
  });
});

describe("isAwaitingUserEntry (侧栏「等你」灯判定)", () => {
  const entry = (over: Partial<InteractionEntry>): InteractionEntry => ({
    id: "x1",
    kind: "approval",
    status: "pending",
    conversationId: "c1",
    messageId: "m1",
    payload: {},
    ...over,
  });

  it("counts hot blocking kinds while pending / submitting", () => {
    expect(isAwaitingUserEntry(entry({ kind: "approval" }))).toBe(true);
    expect(
      isAwaitingUserEntry(entry({ kind: "approval", status: "submitting" })),
    ).toBe(true);
    expect(isAwaitingUserEntry(entry({ kind: "escalation" }))).toBe(true);
    expect(
      isAwaitingUserEntry(
        entry({ kind: "escalation", payload: { awaiting: "user" } }),
      ),
    ).toBe(true);
    expect(isAwaitingUserEntry(entry({ kind: "escalation" }))).toBe(true);
  });

  it("excludes CEO-arbitrated escalations (user has nothing to do)", () => {
    expect(
      isAwaitingUserEntry(
        entry({ kind: "escalation", payload: { awaiting: "ceo" } }),
      ),
    ).toBe(false);
  });

  it("excludes settled entries", () => {
    expect(isAwaitingUserEntry(entry({ status: "resolved" }))).toBe(false);
    expect(isAwaitingUserEntry(entry({ status: "orphaned" }))).toBe(false);
  });

  it("excludes cold kinds (pausedTurns 帧是权威)", () => {
    expect(isAwaitingUserEntry(entry({ kind: "ask_user" }))).toBe(false);
    expect(isAwaitingUserEntry(entry({ kind: "plan_review" }))).toBe(false);
  });
});

describe("escalation_resolved id matching (project frame)", () => {
  it("is covered by InteractionStore markResolved by escalation_id", () => {
    // The project.ts fix matches by f.escalationId; store path uses the same id field.
    applyInteractionWireEvent(
      "escalation_required",
      { escalation_id: "esc-b", question: "B?", assumption: "b" },
      "c1",
      "m1",
    );
    applyInteractionWireEvent(
      "escalation_required",
      { escalation_id: "esc-a", question: "A?", assumption: "a" },
      "c1",
      "m1",
    );
    applyInteractionWireEvent(
      "escalation_resolved",
      { escalation_id: "esc-a", status: "resolved", answer: "yes" },
      "c1",
      "m1",
    );
    expect(store().get("esc-a")?.status).toBe("resolved");
    expect(store().get("esc-b")?.status).toBe("pending");
  });
});

// Journal reload path (replaces retired conversation/projections *FromEvents helpers).
describe("hydrateInteractionsFromJournal (history replay)", () => {
  describe("plan_review", () => {
    it("folds a required→resolved pair into one resolved card", () => {
      hydrateInteractionsFromJournal("a", "m1", [
        {
          type: "plan_review_required",
          payload: {
            checkpoint_id: "c1",
            steps: [{ run_id: "run-1", role: "角色 run-1", summary: "产出" }],
            pending: [{ run_id: "next", role: "下游" }],
          },
        },
        {
          type: "plan_review_resolved",
          payload: { checkpoint_id: "c1", decision: "continue", note: "放行" },
        },
      ]);
      const cards = messagePlanReviews("a", "m1");
      expect(cards).toHaveLength(1);
      expect(cards[0]).toMatchObject({
        id: "c1",
        status: "resolved",
        decision: "continue",
        note: "放行",
      });
      expect(cards[0].steps.map((s) => s.run_id)).toEqual(["run-1"]);
      expect(cards[0].pending.map((p) => p.run_id)).toEqual(["next"]);
    });

    it("keeps an unresolved required as a pending card", () => {
      hydrateInteractionsFromJournal("a", "m1", [
        {
          type: "plan_review_required",
          payload: {
            checkpoint_id: "c1",
            steps: [{ run_id: "run-1", role: "R", summary: "s" }],
            pending: [],
          },
        },
      ]);
      const planEntry = store().get("c1");
      expect(planEntry).toBeDefined();
      if (!planEntry) return;
      expect(entryToPlanReview(planEntry)).toMatchObject({
        status: "pending",
        decision: null,
      });
    });

    it("preserves raise order across multiple checkpoints", () => {
      hydrateInteractionsFromJournal("a", "m1", [
        {
          type: "plan_review_required",
          payload: {
            checkpoint_id: "c1",
            steps: [{ run_id: "run-1", role: "R", summary: "s" }],
            pending: [],
          },
        },
        {
          type: "plan_review_required",
          payload: {
            checkpoint_id: "c2",
            steps: [{ run_id: "run-2", role: "R", summary: "s" }],
            pending: [],
          },
        },
        {
          type: "plan_review_resolved",
          payload: { checkpoint_id: "c1", decision: "stop", note: "" },
        },
      ]);
      const cards = messagePlanReviews("a", "m1");
      expect(cards.map((c) => c.id)).toEqual(["c1", "c2"]);
      expect(cards[0].status).toBe("resolved");
      expect(cards[1].status).toBe("pending");
    });
  });

  describe("ask_user", () => {
    const richPayload = {
      checkpoint_id: "c1",
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
    };

    it("folds a required event into one pending card (rich opening fields)", () => {
      hydrateInteractionsFromJournal("a", "m1", [
        { type: "checkpoint_required", payload: richPayload },
      ]);
      const cards = messageCheckpoints("a", "m1");
      expect(cards).toHaveLength(1);
      const checkpointEntry = store().get("c1");
      expect(checkpointEntry).toBeDefined();
      if (!checkpointEntry) return;
      expect(entryToCheckpoint(checkpointEntry)).toMatchObject({
        id: "c1",
        status: "pending",
        decision: null,
        assumptions: [{ id: "a0", label: "部署", value: "纯静态" }],
      });
      expect(cards[0].questions[0].default).toBe("潜在客户（推荐）");
    });

    it("folds a required→resolved pair into one settled card", () => {
      hydrateInteractionsFromJournal("a", "m1", [
        { type: "checkpoint_required", payload: richPayload },
        {
          type: "checkpoint_resolved",
          payload: {
            checkpoint_id: "c1",
            decision: "continue",
            note: "就按这个开做",
            selected: ["潜在客户（推荐）"],
          },
        },
      ]);
      const cards = messageCheckpoints("a", "m1");
      expect(cards).toHaveLength(1);
      expect(cards[0]).toMatchObject({
        id: "c1",
        status: "resolved",
        decision: "continue",
        note: "就按这个开做",
        selected: ["潜在客户（推荐）"],
      });
    });

    it("preserves raise order across multiple checkpoints", () => {
      hydrateInteractionsFromJournal("a", "m1", [
        {
          type: "checkpoint_required",
          payload: { checkpoint_id: "c1", question: "q1" },
        },
        {
          type: "checkpoint_required",
          payload: { checkpoint_id: "c2", question: "q2" },
        },
        {
          type: "checkpoint_resolved",
          payload: { checkpoint_id: "c1", decision: "stop", note: "" },
        },
      ]);
      const cards = messageCheckpoints("a", "m1");
      expect(cards.map((c) => c.id)).toEqual(["c1", "c2"]);
      expect(cards[0].status).toBe("resolved");
      expect(cards[1].status).toBe("pending");
    });

    it("is empty when the journal has no checkpoint", () => {
      hydrateInteractionsFromJournal("a", "m1", []);
      expect(messageCheckpoints("a", "m1")).toEqual([]);
    });
  });
});

// silence unused vi in case of future mocks
void vi;
