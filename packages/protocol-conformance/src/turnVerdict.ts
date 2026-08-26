// Turn-verdict envelope — protocol-external judgment (`lib/turnOutcome`) that
// the fold golden does not cover. Same fixtures / harness / golden pipeline as
// ProjectedTurn; this is an optional sidecar on the fixture, not a second gate.
//
// Judge encoding is the two desktop fields (`hasTeamStrip` + `supportPackHost`).
// Mobile still drives its own UI with a local `surface`; the envelope must
// translate that into the two fields. `surface` is not part of this sidecar.

import type { ProjectedTurn } from "./projectedTurn";
import { GATE_INTERACTION_KINDS } from "./projectedTurn";

/** Where「复制排查包」hangs. Primary verdict only — never two hosts. */
export type TurnSupportPackHost =
  | "none"
  | "bubble"
  | "strip"
  | "composer"
  | "session";

export type ProjectedTurnVerdict = {
  kind?: "ok" | "partial" | "paused" | "error";
  hideEmptyBubble?: boolean;
  notice?: string | null;
  /** Team strip exists for this turn (scoreboard still up). */
  hasTeamStrip?: boolean | null;
  /** Where「复制排查包」hangs. Orthogonal to {@link hasTeamStrip}. */
  supportPackHost?: TurnSupportPackHost | null;
};

/**
 * Hand-filled golden can invent combos the arbitrator never emits.
 * `bubble` requires no team strip; `strip` requires one.
 */
export function turnVerdictHostContradiction(
  verdict: Pick<ProjectedTurnVerdict, "hasTeamStrip" | "supportPackHost">,
): string | null {
  if (verdict.hasTeamStrip === true && verdict.supportPackHost === "bubble") {
    return 'hasTeamStrip=true 与 supportPackHost="bubble" 互斥（bubble 仅在无团队条时成立）';
  }
  if (verdict.hasTeamStrip === false && verdict.supportPackHost === "strip") {
    return 'hasTeamStrip=false 与 supportPackHost="strip" 互斥（strip 仅在有团队条时成立）';
  }
  return null;
}

export function projectedHasTeamGraph(p: ProjectedTurn): boolean {
  return (p.runs?.length ?? 0) > 0 || (p.process ?? []).some((s) => s.kind === "team");
}

export function projectedHasDedicatedPauseUi(p: ProjectedTurn): boolean {
  return p.interactions.some(
    (i) =>
      (i.status === "pending" || i.status === "resolved") &&
      (GATE_INTERACTION_KINDS as readonly string[]).includes(i.kind),
  );
}
