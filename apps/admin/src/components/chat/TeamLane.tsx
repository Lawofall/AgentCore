import { Badge } from "@/components/ui/Badge";
import type { NormalizedRun } from "@/components/chat/chatTurn";
import { cn } from "@/lib/utils";
import { Users } from "lucide-react";
import { useMemo } from "react";

const STATUS_TONE: Record<
  string,
  "neutral" | "primary" | "success" | "warning" | "destructive"
> = {
  pending: "neutral",
  running: "primary",
  completed: "success",
  failed: "destructive",
  cancelled: "warning",
  skipped: "neutral",
};

/**
 * Static multi-agent final-state tree — no @xyflow / elkjs.
 * Optional `onSelectRun` opens the replay worker dock (ops), not a live canvas.
 */
export function TeamLane({
  runs,
  progress,
  selectedRunId,
  onSelectRun,
}: {
  runs: NormalizedRun[];
  progress: { completed: number; total: number };
  selectedRunId?: string | null;
  onSelectRun?: (runId: string) => void;
}) {
  const { roots, byParent } = useMemo(() => {
    const map = new Map<string | null, NormalizedRun[]>();
    for (const r of runs) {
      const key = r.parentRunId;
      const list = map.get(key) ?? [];
      list.push(r);
      map.set(key, list);
    }
    const known = new Set(runs.map((r) => r.id));
    const orphans = runs.filter(
      (r) => r.parentRunId != null && !known.has(r.parentRunId),
    );
    const top =
      (map.get(null) ?? []).length > 0
        ? (map.get(null) ?? [])
        : orphans.length > 0
          ? orphans
          : runs;
    return { roots: top, byParent: map };
  }, [runs]);

  if (runs.length === 0) return null;
  const completed = progress.completed;
  const total = progress.total || runs.length;

  return (
    <section aria-label="团队" className="min-w-0 max-w-full space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Users size={14} className="text-primary" />
        <span className="font-medium text-foreground">协作图</span>
        <span>
          {completed}/{total} 完成
        </span>
      </div>
      <ul className="space-y-1.5">
        {roots.map((run) => (
          <TeamNode
            key={run.id}
            run={run}
            depth={0}
            byParent={byParent}
            selectedRunId={selectedRunId ?? null}
            onSelectRun={onSelectRun}
          />
        ))}
      </ul>
    </section>
  );
}

function TeamNode({
  run,
  depth,
  byParent,
  selectedRunId,
  onSelectRun,
}: {
  run: NormalizedRun;
  depth: number;
  byParent: Map<string | null, NormalizedRun[]>;
  selectedRunId: string | null;
  onSelectRun?: (runId: string) => void;
}) {
  const children = byParent.get(run.id) ?? [];
  const active = selectedRunId === run.id;
  const body = (
    <>
      <div className="flex min-w-0 items-center gap-2">
        <span
          className="min-w-0 truncate font-medium text-foreground"
          title={run.role || run.agentId || run.id}
        >
          {run.role || run.agentId || run.id}
        </span>
        <Badge className="shrink-0" tone={STATUS_TONE[run.status] ?? "neutral"}>
          {run.status}
        </Badge>
        {run.kind !== "agent" && (
          <span className="shrink-0 text-muted-foreground text-xs">
            {run.kind}
          </span>
        )}
      </div>
      {run.task && (
        <p className="mt-1 truncate text-muted-foreground text-xs" title={run.task}>
          {run.task}
        </p>
      )}
    </>
  );
  return (
    <li>
      {onSelectRun ? (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onSelectRun(run.id);
          }}
          className={cn(
            "min-w-0 w-full rounded-lg border px-3 py-2 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring",
            active
              ? "border-primary/50 bg-primary/10 ring-1 ring-primary/30"
              : "border-border bg-muted/30 hover:bg-muted/50",
          )}
          style={{ marginLeft: depth * 14 }}
        >
          {body}
        </button>
      ) : (
        <div
          className="min-w-0 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm"
          style={{ marginLeft: depth * 14 }}
        >
          {body}
        </div>
      )}
      {children.length > 0 && (
        <ul className="mt-1.5 space-y-1.5">
          {children.map((child) => (
            <TeamNode
              key={child.id}
              run={child}
              depth={depth + 1}
              byParent={byParent}
              selectedRunId={selectedRunId}
              onSelectRun={onSelectRun}
            />
          ))}
        </ul>
      )}
    </li>
  );
}
