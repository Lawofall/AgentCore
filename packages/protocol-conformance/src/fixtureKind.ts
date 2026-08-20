import type { SSEEvent } from "@agentcore/contract-types";
import type { ProjectedTurn } from "./projectedTurn";
import type { ProjectedTurnVerdict } from "./turnVerdict";

/** Minimal shape shared by committed turn-fold conformance vectors. */
export interface TurnFixtureWire {
  name: string;
  description?: string;
  events: SSEEvent[];
  projected: ProjectedTurn;
  /** Optional turnOutcome sidecar; same harness, not a second gate. */
  turnVerdict?: ProjectedTurnVerdict;
}

/** True for turn-fold vectors; false for auxiliary blobs and simulation fold goldens.
 *  Keep this module type-strip-only (`import type`, no runtime deps) so Node
 *  `apps/desktop/scripts/shoot.mjs` can import it without a transpile step. */
export function isTurnFixture(raw: unknown): raw is TurnFixtureWire {
  if (typeof raw !== "object" || raw === null) return false;
  const o = raw as Record<string, unknown>;
  if (typeof o.name !== "string" || !Array.isArray(o.events)) return false;
  const projected = o.projected;
  return (
    typeof projected === "object" &&
    projected !== null &&
    "status" in (projected as Record<string, unknown>)
  );
}
