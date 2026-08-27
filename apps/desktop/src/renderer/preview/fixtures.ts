import type { SSEEvent } from "@/types/events";
import { isTurnFixture } from "@agentcore/protocol-conformance/fixtureKind";
import {
  type FoldReplaySource,
  openEventDocument,
  prepareFoldSource,
} from "./source";

export interface PreviewFixture {
  name: string;
  description: string;
  events: SSEEvent[];
  /** A 路已准备源（与 events 同内容；帧滑块 / shoot 可直接吃）。 */
  source: FoldReplaySource;
}

// Every committed turn-fold conformance vector doubles as a preview scenario.
// Auxiliary blobs (non-turn fixtures) are excluded — same
// contract as protocol-conformance harness `isTurnFixture`. Documents are opened
// through the shared supersets source adapter so tape/recording-shaped inputs
// (and legacy kind/ts dialect) can feed the same replay path.
const modules = import.meta.glob(
  "../../../../../packages/protocol-conformance/fixtures/*.json",
  { eager: true },
) as Record<string, { default: unknown }>;

export const PREVIEW_FIXTURES: PreviewFixture[] = Object.entries(modules)
  .sort(([a], [b]) => a.localeCompare(b))
  .map(([, mod]) => mod.default)
  .filter(isTurnFixture)
  .map((fx) => {
    const doc = openEventDocument(fx);
    const source = prepareFoldSource(fx);
    return {
      name: doc.name ?? fx.name,
      description: doc.description ?? fx.description ?? "",
      events: source.events,
      source,
    };
  });
