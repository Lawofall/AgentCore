import { CopyableId } from "@/components/CopyableId";
import {
  credentialSourceLabel,
  formatProcessSummary,
} from "@/components/conversation-replay/shared";
import { Badge } from "@/components/ui/Badge";
import {
  harvestKindLabel,
  isExecutionHarvestMessage,
} from "@/lib/executionHarvest";
import { cn, fmtCny, fmtMs, nanoToYuan } from "@/lib/utils";
import type { ReplayMessage } from "@/services/adminObservability";
import { Users } from "lucide-react";

/**
 * Turn ops: cost / trace / spans / harvest / workers.
 * Lives in the diagnose dock so the reading column can stay desktop-like.
 */
export function TurnOpsBar({
  selected,
  harvests,
  onSelectHarvest,
  onOpenDock,
}: {
  selected: ReplayMessage | null;
  harvests: ReplayMessage[];
  onSelectHarvest: (id: string) => void;
  /** When omitted, process/worker rows are read-only (already inside the dock). */
  onOpenDock?: () => void;
}) {
  const tools = selected?.spans.filter((s) => s.kind === "tool").length ?? 0;
  const llms = selected?.spans.filter((s) => s.kind !== "tool").length ?? 0;
  const processSummary =
    selected && (tools > 0 || llms > 0)
      ? formatProcessSummary(llms, tools)
      : null;
  const credLabel = selected
    ? credentialSourceLabel(selected.credential_source)
    : null;
  const models = selected?.models ?? [];
  const metrics = selected?.metrics;
  const isHarvest = selected ? isExecutionHarvestMessage(selected) : false;
  const hasWorkers = (selected?.runs.length ?? 0) > 0;
  const hasCost = selected != null && selected.cost_total > 0;
  const hasOps =
    !isHarvest &&
    selected != null &&
    (Boolean(metrics) ||
      hasCost ||
      models.length > 0 ||
      Boolean(credLabel) ||
      Boolean(processSummary) ||
      hasWorkers);

  if (!hasOps && harvests.length === 0) return null;

  return (
    <div aria-label="运维信号" className="flex flex-col gap-2">
      {harvests.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-muted-foreground text-xs font-medium">
            系统收口
          </span>
          {harvests.map((h) => {
            const kind = harvestKindLabel(h.harvest_kind, h.content);
            const active = selected?.id === h.id;
            return (
              <button
                key={h.id}
                type="button"
                aria-current={active ? "true" : undefined}
                onClick={() => onSelectHarvest(h.id)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  active
                    ? "border-primary/40 bg-primary/10 text-foreground"
                    : "border-border bg-card text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                )}
              >
                <span>{kind ?? "收口"}</span>
              </button>
            );
          })}
        </div>
      )}

      {hasOps && selected && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-muted-foreground text-xs">
          {metrics && (
            <Badge
              tone={metrics.status === "error" ? "destructive" : "success"}
            >
              {metrics.finish_reason ?? metrics.status}
            </Badge>
          )}
          {metrics && (
            <span className="tabular-nums">{metrics.rounds} 轮</span>
          )}
          {metrics && (
            <span className="tabular-nums">{fmtMs(metrics.duration_ms)}</span>
          )}
          {hasCost && (
            <span className="tabular-nums">
              {fmtCny(nanoToYuan(selected.cost_total))}
            </span>
          )}
          {metrics?.delegated && (
            <span className="tabular-nums">委派 {metrics.workers} 队员</span>
          )}
          {metrics?.trace_id && (
            <CopyableId
              value={metrics.trace_id}
              label="trace_id"
              display={metrics.trace_id.slice(0, 8)}
              titleHint={`${metrics.trace_id}（点击复制 → log_timeline --trace / --pack）`}
            />
          )}
          {credLabel && <Badge tone="neutral">{credLabel}</Badge>}
          {models.map((m) => (
            <span
              key={m}
              className="rounded-lg border border-border bg-muted/40 px-1.5 py-0.5 font-mono text-xs"
            >
              {m}
            </span>
          ))}
          {processSummary &&
            (onOpenDock ? (
              <button
                type="button"
                onClick={onOpenDock}
                className="rounded text-xs outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              >
                {processSummary}
              </button>
            ) : (
              <span>{processSummary}</span>
            ))}
          {hasWorkers &&
            (onOpenDock ? (
              <button
                type="button"
                aria-label="打开队员面板"
                onClick={onOpenDock}
                className="inline-flex items-center gap-1 rounded-lg border border-border bg-card px-2 py-0.5 text-xs outline-none hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Users size={10} />
                队员 {selected.runs.length}
              </button>
            ) : (
              <span className="inline-flex items-center gap-1">
                <Users size={10} />
                队员 {selected.runs.length}
              </span>
            ))}
        </div>
      )}
    </div>
  );
}
