/**
 * Desktop INTERACTION_REGISTRY live-kind bags.
 *
 * Kind bags are derived from registered rows × INTERACTION_KIND_WIRE flags.
 */
import { INTERACTION_KIND_WIRE } from "@agentcore/contract-types";
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

const REGISTERED = INTERACTION_REGISTRY.map((d) => d.kind);

describe("INTERACTION_REGISTRY live kinds", () => {
  it("registers live UserInteractionKind rows (no kickoff card)", () => {
    expect([...new Set(REGISTERED)].sort()).toEqual(
      [
        "approval",
        "ask_user",
        "escalation",
        "plan_review",
        "stage_card",
      ].sort(),
    );
    expect(REGISTERED).not.toContain("team_preview");
    for (const kind of REGISTERED) {
      expect(INTERACTION_KIND_WIRE[kind]).toBeDefined();
    }
  });
});

describe("kind bags derived from INTERACTION_KIND_WIRE flags", () => {
  it("HOT_INTERACTION_KINDS = hot (current: approval / escalation)", () => {
    expect(HOT_INTERACTION_KINDS).toEqual(["approval", "escalation"]);
    for (const kind of REGISTERED) {
      expect(isHotInteractionKind(kind)).toBe(INTERACTION_KIND_WIRE[kind].hot);
    }
  });

  it("COLD_RESUME_KINDS = pausesTurn && !hot (current: ask_user / plan_review)", () => {
    expect(COLD_RESUME_KINDS).toEqual(["ask_user", "plan_review"]);
    for (const kind of REGISTERED) {
      const w = INTERACTION_KIND_WIRE[kind];
      expect(isColdResumeKind(kind)).toBe(w.pausesTurn && !w.hot);
    }
    expect(isColdResumeKind("team_preview")).toBe(false);
  });

  it("HOT_GATE_INTERACTION_KINDS = hot && pausesTurn (current: approval)", () => {
    expect(HOT_GATE_INTERACTION_KINDS).toEqual(["approval"]);
    for (const kind of REGISTERED) {
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
    for (const kind of REGISTERED) {
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
    expect(submitPathOf("stage_card")).toBe("stage");
    for (const kind of REGISTERED) {
      const w = INTERACTION_KIND_WIRE[kind];
      const path = submitPathOf(kind);
      if (w.hot) expect(path).toBe("hot");
      else if (w.pausesTurn) expect(path).toBe("cold");
      else if (w.reconnectAnswerable) expect(path).toBe("stage");
      else throw new Error(`unexpected leftover submit path for ${kind}`);
    }
  });
});
