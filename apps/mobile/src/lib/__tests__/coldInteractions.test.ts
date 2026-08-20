import {
  INTERACTION_KIND_WIRE,
  USER_INTERACTION_KIND_VALUES,
} from "@agentcore/contract-types";
import { beforeEach, describe, expect, it } from "vitest";
import {
  COLD_RESUME_KINDS,
  applyColdInteractionWireEvent,
  bindEmptyColdMessageId,
  clearColdInteractions,
  getColdInteraction,
  idFromColdRequiredPayload,
  isColdResumeKind,
  kindFromColdRequiredEvent,
  kindFromColdResolvedEvent,
  listColdPending,
  markColdDeferred,
  markColdResolved,
  markColdSubmitting,
  rekeyColdMessageId,
  reopenColdPending,
  upsertColdRequired,
} from "../coldInteractions";

beforeEach(() => {
  clearColdInteractions();
});

describe("COLD_RESUME_KINDS · pausesTurn && !hot", () => {
  it("membership matches the generated flags (current: ask_user / plan_review / team_preview)", () => {
    expect(COLD_RESUME_KINDS).toEqual([
      "ask_user",
      "plan_review",
      "team_preview",
    ]);
    for (const kind of USER_INTERACTION_KIND_VALUES) {
      const wire = INTERACTION_KIND_WIRE[kind];
      expect(isColdResumeKind(kind)).toBe(wire.pausesTurn && !wire.hot);
    }
  });

  it("required / resolved events and idField come from INTERACTION_KIND_WIRE", () => {
    expect(kindFromColdRequiredEvent("checkpoint_required")).toBe("ask_user");
    expect(kindFromColdRequiredEvent("plan_review_required")).toBe(
      "plan_review",
    );
    expect(kindFromColdRequiredEvent("team_preview_required")).toBe(
      "team_preview",
    );
    expect(kindFromColdRequiredEvent("approval_required")).toBeNull();
    expect(kindFromColdRequiredEvent("escalation_required")).toBeNull();
    expect(kindFromColdRequiredEvent("stage_card_required")).toBeNull();

    expect(kindFromColdResolvedEvent("checkpoint_resolved")).toBe("ask_user");
    expect(kindFromColdResolvedEvent("plan_review_resolved")).toBe(
      "plan_review",
    );
    expect(kindFromColdResolvedEvent("team_preview_resolved")).toBe(
      "team_preview",
    );
    expect(kindFromColdResolvedEvent("approval_resolved")).toBeNull();
    expect(kindFromColdResolvedEvent("escalation_resolved")).toBeNull();

    expect(
      idFromColdRequiredPayload("ask_user", { checkpoint_id: "cp-1" }),
    ).toBe("cp-1");
    expect(
      idFromColdRequiredPayload("plan_review", { checkpoint_id: "pr-1" }),
    ).toBe("pr-1");
    expect(
      idFromColdRequiredPayload("ask_user", { approval_id: "a1" }),
    ).toBeNull();
  });
});

describe("coldInteractions · upsertRequired tombstones", () => {
  it("upserts ask_user / plan_review / team_preview required", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "cp1", question: "继续吗？" },
    });
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "c1",
      messageId: "m1",
      payload: {
        checkpoint_id: "tp1",
        primitive: "delegate",
        workers: [],
      },
    });
    expect(getColdInteraction("cp1")?.status).toBe("pending");
    expect(listColdPending("c1")).toHaveLength(2);
  });

  it("resolved stub yields to a live required payload", () => {
    markColdResolved({
      kind: "ask_user",
      id: "cp-stub",
      resolution: { decision: "continue" },
    });
    expect(getColdInteraction("cp-stub")?.payload).toEqual({});

    upsertColdRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m2",
      payload: { checkpoint_id: "cp-stub", question: "还要拍板吗？" },
    });
    expect(getColdInteraction("cp-stub")?.status).toBe("pending");
    expect(getColdInteraction("cp-stub")?.messageId).toBe("m2");
    expect(
      (getColdInteraction("cp-stub")?.payload as { question?: string })
        .question,
    ).toBe("还要拍板吗？");
  });

  it("cold required on a new host messageId replaces a prior resolved entry (round 2+)", () => {
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "c1",
      messageId: "m-turn1",
      payload: {
        checkpoint_id: "tp-reuse",
        primitive: "delegate",
        workers: [],
      },
    });
    markColdResolved({
      kind: "team_preview",
      id: "tp-reuse",
      resolution: { decision: "continue" },
    });
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "c1",
      messageId: "m-turn2",
      payload: {
        checkpoint_id: "tp-reuse",
        primitive: "delegate",
        workers: [{ run_id: "r2", role: "研", task: "t", depends_on: [] }],
      },
    });
    expect(getColdInteraction("tp-reuse")?.status).toBe("pending");
    expect(getColdInteraction("tp-reuse")?.messageId).toBe("m-turn2");
  });

  it("same-host settled replay stays blocked", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "cp-same", question: "一？" },
    });
    markColdResolved({ kind: "ask_user", id: "cp-same" });
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "cp-same", question: "重放？" },
    });
    expect(getColdInteraction("cp-same")?.status).toBe("resolved");
    expect(
      (getColdInteraction("cp-same")?.payload as { question?: string })
        .question,
    ).toBe("一？");
  });

  it("status:pending force replaces a resolved cold entry (recovery)", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "cp-force", question: "旧" },
    });
    markColdResolved({ kind: "ask_user", id: "cp-force" });
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "cp-force", question: "恢复" },
      status: "pending",
    });
    expect(getColdInteraction("cp-force")?.status).toBe("pending");
    expect(
      (getColdInteraction("cp-force")?.payload as { question?: string })
        .question,
    ).toBe("恢复");
  });
});

describe("coldInteractions · stamp rekey / bind", () => {
  it("rekeys client bubble id to server message id", () => {
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "c1",
      messageId: "client-uuid",
      payload: {
        checkpoint_id: "tp-rekey",
        primitive: "delegate",
        workers: [],
      },
    });
    rekeyColdMessageId("client-uuid", "m-server");
    expect(getColdInteraction("tp-rekey")?.messageId).toBe("m-server");
  });

  it("binds empty messageId on stamp", () => {
    upsertColdRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "",
      payload: { checkpoint_id: "pr-bind", steps: [], pending: [] },
    });
    bindEmptyColdMessageId("c1", "m-server-late");
    expect(getColdInteraction("pr-bind")?.messageId).toBe("m-server-late");
  });
});

describe("coldInteractions · wire events", () => {
  it("applies team_preview_required / resolved", () => {
    applyColdInteractionWireEvent(
      "team_preview_required",
      {
        checkpoint_id: "tp-wire",
        primitive: "delegate",
        workers: [{ run_id: "r1", role: "研", task: "t", depends_on: [] }],
      },
      "c1",
      "m1",
    );
    expect(getColdInteraction("tp-wire")?.status).toBe("pending");
    applyColdInteractionWireEvent(
      "team_preview_resolved",
      { checkpoint_id: "tp-wire", decision: "continue" },
      "c1",
      "m1",
    );
    expect(getColdInteraction("tp-wire")?.status).toBe("resolved");
  });
});

describe("coldInteractions · resume_deferred submitting", () => {
  it("markColdSubmitting then resume_deferred locks busy_reason", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m-def",
      payload: { checkpoint_id: "cp-def", question: "继续？" },
    });
    expect(
      markColdSubmitting({
        kind: "ask_user",
        id: "cp-def",
        resolution: { decision: "continue" },
      }),
    ).toBe(true);
    expect(getColdInteraction("cp-def")?.status).toBe("submitting");

    markColdDeferred({
      messageId: "m-def",
      conversationId: "c1",
      busyReason: "live_turn",
    });
    expect(getColdInteraction("cp-def")?.deferredBusyReason).toBe("live_turn");
    expect(getColdInteraction("cp-def")?.status).toBe("submitting");
    expect(listColdPending("c1")).toHaveLength(1);
  });

  it("prewrite *_resolved while submitting keeps card submitting", () => {
    upsertColdRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "m-pr",
      payload: { checkpoint_id: "pr-def", question: "" },
    });
    markColdSubmitting({
      kind: "plan_review",
      id: "pr-def",
      resolution: { decision: "continue" },
    });
    markColdDeferred({
      messageId: "m-pr",
      busyReason: "wrap_up",
    });
    applyColdInteractionWireEvent(
      "plan_review_resolved",
      { checkpoint_id: "pr-def", decision: "continue" },
      "c1",
      "m-pr",
    );
    expect(getColdInteraction("pr-def")?.status).toBe("submitting");
    expect(getColdInteraction("pr-def")?.deferredBusyReason).toBe("wrap_up");
    expect(getColdInteraction("pr-def")?.resolution).toMatchObject({
      decision: "continue",
    });
  });

  it("reopenColdPending restores editable pending after refuse", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m-re",
      payload: { checkpoint_id: "cp-re", question: "?" },
    });
    markColdSubmitting({ kind: "ask_user", id: "cp-re" });
    markColdDeferred({ messageId: "m-re", busyReason: "wrap_up" });
    reopenColdPending("cp-re");
    expect(getColdInteraction("cp-re")?.status).toBe("pending");
    expect(getColdInteraction("cp-re")?.deferredBusyReason).toBeUndefined();
  });
});
