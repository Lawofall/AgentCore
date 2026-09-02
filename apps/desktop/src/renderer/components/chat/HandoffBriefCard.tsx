import { Markdown } from "@/components/chat/Markdown";
import {
  hasDebriefDetails,
  isDegradedDebrief,
} from "@/components/chat/handoffBrief";
import { Button } from "@/components/ui";
import { usePersistentDisclosure } from "@/stores/disclosure";
import type { RunDebrief } from "@/types/events";
import { ChevronDown, ChevronRight } from "lucide-react";

const DEGRADED_NOTICE = "简报由系统降级生成";

const BODY_INSET = "mt-1.5 space-y-2 rounded-lg bg-muted px-2.5 py-1.5";

/**
 * Human-facing 交接简报 — same chrome for a successful `handoff` tool row
 * and the run-detail footer (degraded / harvest-without-success-step).
 * Collapsed face is a process row (chevron +「交接简报」, no muted plate).
 * Expand reveals `summary` as markdown in a muted inset,
 * then historical 要点 / 假设 / 下一步 when those fields are present.
 * Engine-synthesized (`degraded`) briefs show a notice only, already inset.
 */
export function HandoffBriefCard({
  debrief,
  persistKey = null,
}: {
  debrief: RunDebrief;
  persistKey?: string | null;
}) {
  const degraded = isDegradedDebrief(debrief);
  const details = !degraded && hasDebriefDetails(debrief);
  const [open, setOpen] = usePersistentDisclosure(persistKey, false);
  const summary = debrief.summary?.trim() ?? "";
  const expandable = !degraded && (Boolean(summary) || details);

  return (
    <div className="min-w-0 max-w-full">
      {degraded ? (
        <div className="rounded-lg bg-muted px-2.5 py-1.5">
          <h3 className="text-sm text-muted-foreground">交接简报</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {DEGRADED_NOTICE}
          </p>
        </div>
      ) : expandable ? (
        <>
          <Button
            variant="ghost"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="h-auto w-full items-center justify-start gap-2 px-0 py-0 text-sm text-muted-foreground hover:bg-transparent hover:text-foreground"
          >
            {open ? (
              <ChevronDown size={14} className="shrink-0" />
            ) : (
              <ChevronRight size={14} className="shrink-0" />
            )}
            交接简报
          </Button>
          {open && (
            <div className={BODY_INSET}>
              {summary ? <Markdown content={summary} /> : null}
              {details ? <DebriefDetails debrief={debrief} /> : null}
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

export function DebriefDetails({ debrief }: { debrief: RunDebrief }) {
  const { key_points, assumptions, next_steps } = debrief;
  return (
    <div className="space-y-2">
      {key_points && key_points.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">
            关键要点
          </p>
          <ul className="list-disc space-y-0.5 pl-4 text-sm break-words text-foreground">
            {key_points.map((pt, i) => (
              <li key={`${i}:${pt}`}>{pt}</li>
            ))}
          </ul>
        </div>
      )}
      {assumptions && (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">
            关键假设
          </p>
          <p className="whitespace-pre-wrap break-words text-sm text-foreground">
            {assumptions}
          </p>
        </div>
      )}
      {next_steps && (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">
            建议下一步
          </p>
          <Markdown content={next_steps} />
        </div>
      )}
    </div>
  );
}
