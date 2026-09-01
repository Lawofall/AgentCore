import { HandoffBriefCard } from "@/components/chat/HandoffBriefCard";
import type { RunDebrief } from "@/types/events";

export { DebriefDetails } from "@/components/chat/HandoffBriefCard";

/**
 * Footer 交接简报 — same card as a successful `handoff` tool row.
 * Rendered only when the process has no successful handoff (degraded synth
 * or harvest without a success step).
 */
export function DebriefSection({ debrief }: { debrief: RunDebrief }) {
  return (
    <section className="mb-4 last:mb-0">
      <HandoffBriefCard debrief={debrief} />
    </section>
  );
}
