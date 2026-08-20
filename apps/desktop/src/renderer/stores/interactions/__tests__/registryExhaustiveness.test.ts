/**
 * Desktop INTERACTION_REGISTRY ⊇ wire UserInteractionKind exhaustiveness.
 *
 * Adding a wire kind without a registry row = silent missing card. Compile-time
 * Record typing does not fail the build when a kind is omitted from the array.
 *
 * Kind bags are derived from INTERACTION_KIND_WIRE flags (not hand-copied names).
 * The expected-member locks below match current specs — flag changes must update
 * callers, not silently widen a bag.
 */
import {
  INTERACTION_KIND_WIRE,
  USER_INTERACTION_KIND_VALUES,
} from "@agentcore/contract-types";
import { INTERACTION_CARD_NAME as SHARED_INTERACTION_CARD_NAME } from "@agentcore/protocol-fold-kit";
import { describe, expect, it } from "vitest";
import {
  COLD_RESUME_KINDS,
  HOT_GATE_INTERACTION_KINDS,
  HOT_INTERACTION_KINDS,
  INTERACTION_CARD_NAME,
  INTERACTION_REGISTRY,
  STAGE_INTERACTION_KINDS,
  hotGateKindTitle,
  isColdResumeKind,
  isHotGateInteractionKind,
  isHotInteractionKind,
  isStageInteractionKind,
  submitPathOf,
} from "../registry";

describe("INTERACTION_REGISTRY wire exhaustiveness", () => {
  it("registers every UserInteractionKind from codegen wire", () => {
    const registered = new Set(INTERACTION_REGISTRY.map((d) => d.kind));
    const wireKinds = new Set(USER_INTERACTION_KIND_VALUES);

    expect(registered).toEqual(wireKinds);
    for (const kind of USER_INTERACTION_KIND_VALUES) {
      expect(INTERACTION_KIND_WIRE[kind]).toBeDefined();
    }
  });
});

describe("kind bags derived from INTERACTION_KIND_WIRE flags", () => {
  it("HOT_INTERACTION_KINDS = hot (current: approval / escalation)", () => {
    expect(HOT_INTERACTION_KINDS).toEqual(["approval", "escalation"]);
    for (const kind of USER_INTERACTION_KIND_VALUES) {
      expect(isHotInteractionKind(kind)).toBe(INTERACTION_KIND_WIRE[kind].hot);
    }
  });

  it("COLD_RESUME_KINDS = pausesTurn && !hot (current: ask_user / plan_review / team_preview)", () => {
    expect(COLD_RESUME_KINDS).toEqual([
      "ask_user",
      "plan_review",
      "team_preview",
    ]);
    for (const kind of USER_INTERACTION_KIND_VALUES) {
      const w = INTERACTION_KIND_WIRE[kind];
      expect(isColdResumeKind(kind)).toBe(w.pausesTurn && !w.hot);
    }
  });

  it("HOT_GATE_INTERACTION_KINDS = hot && pausesTurn (current: approval)", () => {
    expect(HOT_GATE_INTERACTION_KINDS).toEqual(["approval"]);
    for (const kind of USER_INTERACTION_KIND_VALUES) {
      const w = INTERACTION_KIND_WIRE[kind];
      expect(isHotGateInteractionKind(kind)).toBe(w.hot && w.pausesTurn);
    }
  });

  it("INTERACTION_CARD_NAME is the shared kit table; unknown keys do not inherit 工具审批", () => {
    expect(INTERACTION_CARD_NAME).toBe(SHARED_INTERACTION_CARD_NAME);
    expect(hotGateKindTitle("approval")).toBe("工具审批");
    expect(hotGateKindTitle("synthetic_hot_gate")).toBe("synthetic_hot_gate");
    expect(hotGateKindTitle("synthetic_hot_gate")).not.toBe(
      INTERACTION_CARD_NAME.approval,
    );
  });

  it("STAGE_INTERACTION_KINDS = reconnectAnswerable && !hot && !pausesTurn (current: stage_card)", () => {
    expect(STAGE_INTERACTION_KINDS).toEqual(["stage_card"]);
    for (const kind of USER_INTERACTION_KIND_VALUES) {
      const w = INTERACTION_KIND_WIRE[kind];
      expect(isStageInteractionKind(kind)).toBe(
        w.reconnectAnswerable && !w.hot && !w.pausesTurn,
      );
    }
  });

  it("submitPathOf matches the flag priority (hot / cold / stage)", () => {
    expect(submitPathOf("approval")).toBe("hot");
    expect(submitPathOf("escalation")).toBe("hot");
    expect(submitPathOf("ask_user")).toBe("cold");
    expect(submitPathOf("plan_review")).toBe("cold");
    expect(submitPathOf("team_preview")).toBe("cold");
    expect(submitPathOf("stage_card")).toBe("stage");
    for (const kind of USER_INTERACTION_KIND_VALUES) {
      const w = INTERACTION_KIND_WIRE[kind];
      const path = submitPathOf(kind);
      if (w.hot) expect(path).toBe("hot");
      else if (w.pausesTurn) expect(path).toBe("cold");
      else if (w.reconnectAnswerable) expect(path).toBe("stage");
      else throw new Error(`unexpected leftover submit path for ${kind}`);
    }
  });
});
