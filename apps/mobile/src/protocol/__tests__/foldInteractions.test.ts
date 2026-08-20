import { INTERACTION_KIND_WIRE } from "@agentcore/contract-types";
import type { ProjectedInteraction } from "@agentcore/protocol-conformance/projectedTurn";
import { describe, expect, it } from "vitest";
import { hasGatePending } from "../foldInteractions";

function pending(kind: ProjectedInteraction["kind"]): ProjectedInteraction {
  return { kind, id: "x", status: "pending" } as ProjectedInteraction;
}

describe("hasGatePending · pausesTurn", () => {
  it("pending + pausesTurn is a gate; other flags are not", () => {
    expect(hasGatePending([pending("approval")])).toBe(true);
    expect(hasGatePending([pending("ask_user")])).toBe(true);
    expect(hasGatePending([pending("plan_review")])).toBe(true);
    expect(hasGatePending([pending("team_preview")])).toBe(true);
    expect(hasGatePending([pending("escalation")])).toBe(false);
    expect(hasGatePending([pending("stage_card")])).toBe(false);
  });

  it("resolved / orphaned never pause, even on a gate kind", () => {
    expect(
      hasGatePending([
        {
          kind: "approval",
          id: "x",
          status: "resolved",
        } as ProjectedInteraction,
      ]),
    ).toBe(false);
    expect(
      hasGatePending([
        {
          kind: "ask_user",
          id: "x",
          status: "orphaned",
        } as ProjectedInteraction,
      ]),
    ).toBe(false);
  });

  it("matches INTERACTION_KIND_WIRE.pausesTurn for every kind", () => {
    for (const kind of Object.keys(INTERACTION_KIND_WIRE) as Array<
      keyof typeof INTERACTION_KIND_WIRE
    >) {
      expect(hasGatePending([pending(kind)])).toBe(
        INTERACTION_KIND_WIRE[kind].pausesTurn,
      );
    }
  });
});
