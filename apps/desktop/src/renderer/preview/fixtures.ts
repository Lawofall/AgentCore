import type { SSEEvent } from "@/types/events";
import { isPreviewFixture } from "@agentcore/protocol-conformance/fixtureKind";
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

// Committed turn-fold vectors with `preview !== false` are preview scenarios.
// `preview: false` stays in the conformance harness (`isTurnFixture`) only.
// Documents are opened through the shared supersets source adapter so
// tape/recording-shaped inputs can feed the same replay path.
const modules = import.meta.glob(
  "../../../../../packages/protocol-conformance/fixtures/*.json",
  { eager: true },
) as Record<string, { default: unknown }>;

export const PREVIEW_FIXTURES: PreviewFixture[] = Object.entries(modules)
  .sort(([a], [b]) => a.localeCompare(b))
  .map(([, mod]) => mod.default)
  .filter(isPreviewFixture)
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
