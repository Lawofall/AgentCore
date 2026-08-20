// Conformance harness — runs a frontend `fold` against the backend-exported golden
// vectors and reports ProjectedTurn drift (前端技术与架构 §十 SSE 与协议一致性).
//
// Vectors + golden are committed JSON under ./fixtures/, produced by the backend
// oracle (the single source: runtime/conformance/export.py). This package holds NO
// app code — each app runs its own `conformance` script that calls runConformance()
// with its fold, so the dependency points apps → this package (never the reverse).

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { SSEEvent } from "@agentcore/contract-types";
import { hasProjectedFailureFace } from "./failureFace";
import type { ProjectedTurn } from "./projectedTurn";
import { isTurnFixture, type TurnFixtureWire } from "./fixtureKind";
import {
  type ProjectedTurnVerdict,
  turnVerdictHostContradiction,
} from "./turnVerdict";
import {
  TURN_VERDICT_KNOWN_GAPS,
  knownGapFieldsFor,
  turnVerdictDiffField,
} from "./turnVerdictGaps";

/** A frontend's protocol fold under test: events[] → normalized ProjectedTurn. */
export type Fold = (events: SSEEvent[]) => ProjectedTurn;

/** Optional turnOutcome adapter: folded turn → comparison envelope. */
export type Verdict = (
  events: SSEEvent[],
  projected: ProjectedTurn,
) => ProjectedTurnVerdict;

/** One committed conformance case: a real-shaped event sequence + the backend
 * oracle's expected projection. */
export type Fixture = TurnFixtureWire;

const FIXTURES_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "fixtures");

/** Load every committed turn-fold golden fixture (sorted by name for stable output). */
export function loadFixtures(): Fixture[] {
  let files: string[];
  try {
    files = readdirSync(FIXTURES_DIR).filter((f) => f.endsWith(".json"));
  } catch {
    throw new Error(
      `conformance: fixtures dir not found at ${FIXTURES_DIR} — run \`python -m agentcore.conformance.export\` (backend) to generate golden.`,
    );
  }
  return files
    .sort()
    .map((f) => JSON.parse(readFileSync(join(FIXTURES_DIR, f), "utf8")))
    .filter(isTurnFixture);
}

/** Structured comparison: the list of leaf field paths where `actual` diverges from
 * `golden`, each as `path: golden=… actual=…`. Empty ⇒ conformant. Designed for an
 * agent to read: it points at the exact diverging field/run/status, no other end to load. */
export function diffProjected(golden: unknown, actual: unknown): string[] {
  const out: string[] = [];
  walk(golden, actual, "", out);
  return out;
}

function walk(golden: unknown, actual: unknown, path: string, out: string[]): void {
  if (golden === actual) return;
  if (
    golden === null ||
    actual === null ||
    typeof golden !== "object" ||
    typeof actual !== "object"
  ) {
    if (!Object.is(golden, actual)) {
      out.push(`${path || "(root)"}: golden=${fmt(golden)} actual=${fmt(actual)}`);
    }
    return;
  }
  const goldArr = Array.isArray(golden);
  const actArr = Array.isArray(actual);
  if (goldArr !== actArr) {
    out.push(`${path || "(root)"}: golden=${fmt(golden)} actual=${fmt(actual)}`);
    return;
  }
  if (goldArr && actArr) {
    if (golden.length !== actual.length) {
      out.push(`${path}.length: golden=${golden.length} actual=${actual.length}`);
    }
    const n = Math.max(golden.length, actual.length);
    for (let i = 0; i < n; i++) walk(golden[i], actual[i], `${path}[${i}]`, out);
    return;
  }
  const g = golden as Record<string, unknown>;
  const a = actual as Record<string, unknown>;
  for (const key of new Set([...Object.keys(g), ...Object.keys(a)])) {
    walk(g[key], a[key], path ? `${path}.${key}` : key, out);
  }
}

function fmt(v: unknown): string {
  const s = JSON.stringify(v);
  if (s === undefined) return String(v);
  return s.length > 120 ? `${s.slice(0, 117)}…` : s;
}

export interface ConformanceResult {
  passed: number;
  failed: number;
}

/** Diff only keys the golden asked for (partial sidecar). Extra actual fields are ignored. */
export function diffTurnVerdict(
  golden: Record<string, unknown>,
  actual: Record<string, unknown>,
): string[] {
  const sliced: Record<string, unknown> = {};
  for (const key of Object.keys(golden)) {
    sliced[key] = actual[key];
  }
  return diffProjected(golden, sliced).map((d) =>
    d.startsWith("turnVerdict.") ? d : `turnVerdict.${d}`,
  );
}

/**
 * Run one fold against every fixture, print a single red/green report with
 * ProjectedTurn diffs, and set process.exitCode on any drift (CI gate). Returns the
 * tallies so a caller can aggregate multiple folds. Optional ``verdict`` diffs the
 * turnOutcome sidecar when the fixture carries ``turnVerdict``.
 */
export function runConformance(impl: {
  name: string;
  fold: Fold;
  verdict?: Verdict;
}): ConformanceResult {
  const fixtures = loadFixtures();
  let passed = 0;
  let failed = 0;
  console.log(`\nconformance · ${impl.name} · ${fixtures.length} vectors`);
  for (const fx of fixtures) {
    let diffs: string[];
    let actual: ProjectedTurn | null = null;
    try {
      actual = impl.fold(fx.events);
      diffs = diffProjected(fx.projected, actual);
    } catch (e) {
      diffs = [`(threw) ${e instanceof Error ? e.stack ?? e.message : String(e)}`];
    }
    // Empty-face redesign: empty_face_* vectors must fold to a non-empty face
    // (structured error / failure finish; short exemptions in hasProjectedFailureFace).
    if (fx.name.startsWith("empty_face_") && actual && diffs.length === 0) {
      if (!hasProjectedFailureFace(fx.projected)) {
        diffs.push("golden projected missing failure face (empty_face_*)");
      }
      if (!hasProjectedFailureFace(actual)) {
        diffs.push("fold projected missing failure face (empty_face_*)");
      }
    }
    // turnOutcome sidecar: same fixture / same diff, optional envelope on the vector.
    // Known-gap ledger is field-level only (turnVerdictGaps.ts); unregistered drift stays red.
    if (fx.turnVerdict) {
      const goldenHostBad = turnVerdictHostContradiction(fx.turnVerdict);
      if (goldenHostBad) {
        diffs.push(`turnVerdict: ${goldenHostBad}`);
      }
      if (!impl.verdict) {
        diffs.push("turnVerdict: impl did not register a verdict adapter");
      } else if (actual) {
        try {
          const got = impl.verdict(fx.events, actual);
          const actualHostBad = turnVerdictHostContradiction(got);
          if (actualHostBad) {
            diffs.push(`turnVerdict: ${actualHostBad}`);
          }
          const verdictDiffs = diffTurnVerdict(
            fx.turnVerdict as Record<string, unknown>,
            got as Record<string, unknown>,
          );
          const registered = knownGapFieldsFor(impl.name, fx.name);
          const seen = new Set<string>();
          for (const d of verdictDiffs) {
            const field = turnVerdictDiffField(d);
            if (field && registered.has(field)) {
              seen.add(field);
              continue;
            }
            diffs.push(d);
          }
          for (const field of registered) {
            if (!seen.has(field)) {
              diffs.push(
                `turnVerdict.${field}: 登记的 known gap 已愈合（本端不再漂移），请从 TURN_VERDICT_KNOWN_GAPS 删掉该字段`,
              );
            }
          }
        } catch (e) {
          diffs.push(
            `turnVerdict (threw) ${e instanceof Error ? e.stack ?? e.message : String(e)}`,
          );
        }
      }
    }
    if (diffs.length === 0) {
      passed++;
      console.log(`  ✓ ${fx.name}`);
    } else {
      failed++;
      console.log(`  ✗ ${fx.name} — ${fx.description}`);
      for (const d of diffs.slice(0, 20)) console.log(`      ${d}`);
      if (diffs.length > 20) console.log(`      …(+${diffs.length - 20} more)`);
    }
  }
  const byName = new Map(fixtures.map((f) => [f.name, f]));
  for (const gap of TURN_VERDICT_KNOWN_GAPS) {
    const fields = knownGapFieldsFor(impl.name, gap.fixture);
    if (fields.size === 0) continue;
    const booked = byName.get(gap.fixture);
    if (!booked) {
      failed++;
      console.log(
        `  ✗ ${gap.fixture} — turnVerdict known-gap 指向不存在的向量，请从 TURN_VERDICT_KNOWN_GAPS 删掉`,
      );
    } else if (!booked.turnVerdict) {
      failed++;
      console.log(
        `  ✗ ${gap.fixture} — turnVerdict known-gap 指向无 sidecar 的向量，请从 TURN_VERDICT_KNOWN_GAPS 删掉`,
      );
    }
  }
  if (failed === 0 && TURN_VERDICT_KNOWN_GAPS.length > 0) {
    console.log(
      `  known gaps (${TURN_VERDICT_KNOWN_GAPS.length} simplified/impossible — documented, not failures):`,
    );
    for (const g of TURN_VERDICT_KNOWN_GAPS) {
      console.log(`  ○ [turnVerdict] ${g.fixture} — ${g.verdict}: ${g.reason}`);
    }
    console.log(
      `  PASS (${passed}/${fixtures.length}; 0 problems; ${TURN_VERDICT_KNOWN_GAPS.length} known gaps)`,
    );
  } else {
    console.log(`  ${failed === 0 ? "PASS" : "FAIL"} (${passed}/${fixtures.length})`);
  }
  if (failed > 0) process.exitCode = 1;
  return { passed, failed };
}
