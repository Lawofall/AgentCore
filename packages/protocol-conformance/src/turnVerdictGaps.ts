// turnVerdict known-gap ledger — field-level only (never a whole-vector waiver).
// Same inventory shape as mobile parity (`simplified` / `impossible` + reason).
// Ratchet: a registered field that no longer diffs is a failure (must delete it);
// an unregistered field that diffs is still a failure. The table can only shrink.

import type { ProjectedTurnVerdict } from "./turnVerdict";

export type TurnVerdictGapVerdict = "simplified" | "impossible";

export type TurnVerdictImpl = "desktop" | "mobile";

export interface TurnVerdictKnownGap {
  fixture: string;
  /** First path segment under `turnVerdict` (covers `.length` / `[i]` subtree). */
  fields: Partial<Record<TurnVerdictImpl, readonly (keyof ProjectedTurnVerdict)[]>>;
  verdict: TurnVerdictGapVerdict;
  reason: string;
}

export const TURN_VERDICT_KNOWN_GAPS: readonly TurnVerdictKnownGap[] = [];

export function knownGapFieldsFor(impl: string, fixture: string): ReadonlySet<string> {
  const out = new Set<string>();
  if (impl !== "desktop" && impl !== "mobile") return out;
  for (const gap of TURN_VERDICT_KNOWN_GAPS) {
    if (gap.fixture !== fixture) continue;
    for (const field of gap.fields[impl] ?? []) out.add(field);
  }
  return out;
}

/** `turnVerdict.notice: …` → `notice`. */
export function turnVerdictDiffField(diff: string): string | null {
  const rest = diff.startsWith("turnVerdict.") ? diff.slice("turnVerdict.".length) : diff;
  const path = rest.split(":")[0] ?? "";
  const m = path.match(/^([A-Za-z_][A-Za-z0-9_]*)/);
  return m?.[1] ?? null;
}
