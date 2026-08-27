import type { NormalizedRun } from "@/components/chat/chatTurn";
import { cn } from "@/lib/utils";
import { CheckCircle2, Circle, Loader2, Square, XCircle } from "lucide-react";
import { useMemo } from "react";

const STATUS_DOT: Record<string, string> = {
  pending: "bg-muted-foreground/40",
  running: "bg-primary",
  completed: "bg-success",
  failed: "bg-destructive",
  cancelled: "bg-warning",
  skipped: "bg-muted-foreground/30",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "排队中",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已停止",
  skipped: "未执行",
};

function teamLifecycle(runs: NormalizedRun[]): {
  label: string;
  tone: "success" | "primary" | "destructive" | "warning" | "neutral";
} {
  if (runs.length === 0) return { label: "协作", tone: "neutral" };
  const statuses = runs.map((r) => r.status);
  if (statuses.every((s) => s === "completed"))
    return { label: "完成", tone: "success" };
  if (statuses.some((s) => s === "failed"))
    return { label: "失败", tone: "destructive" };
  if (statuses.some((s) => s === "cancelled"))
    return { label: "已停止", tone: "warning" };
  if (statuses.some((s) => s === "running"))
    return { label: "执行中", tone: "primary" };
  return { label: "协作", tone: "neutral" };
}

function LifecycleIcon({
  tone,
  label,
}: {
  tone: "success" | "primary" | "destructive" | "warning" | "neutral";
  label: string;
}) {
  const icon =
    tone === "success" ? (
      <CheckCircle2 size={14} className="shrink-0 text-success" aria-hidden />
    ) : tone === "primary" ? (
      <Loader2 size={14} className="shrink-0 animate-spin text-primary" aria-hidden />
    ) : tone === "destructive" ? (
      <XCircle size={14} className="shrink-0 text-destructive" aria-hidden />
    ) : tone === "warning" ? (
      <Square size={14} className="shrink-0 text-muted-foreground" aria-hidden />
    ) : (
      <Circle size={14} className="shrink-0 text-muted-foreground" aria-hidden />
    );
  return (
    <span className="inline-flex shrink-0" role="img" aria-label={label} title={label}>
      {icon}
    </span>
  );
}

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
  const lifecycle = teamLifecycle(runs);

  return (
    <section aria-label="团队" className="min-w-0 max-w-full space-y-2">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-xs">
        <LifecycleIcon tone={lifecycle.tone} label={lifecycle.label} />
        <span className="tabular-nums text-muted-foreground">{`${completed}/${total}`}</span>
      </div>
      <ul className="space-y-1">
        {roots.map((run) => (
          <TeamNode
            key={run.id}
            run={run}
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
  byParent,
  selectedRunId,
  onSelectRun,
}: {
  run: NormalizedRun;
  byParent: Map<string | null, NormalizedRun[]>;
  selectedRunId: string | null;
  onSelectRun?: (runId: string) => void;
}) {
  const children = byParent.get(run.id) ?? [];
  const active = selectedRunId === run.id;
  const role = run.role || run.agentId || run.id;
  const statusLabel = STATUS_LABEL[run.status] ?? run.status;
  const dotClass = STATUS_DOT[run.status] ?? "bg-muted-foreground/40";

  const card = (
    <div className="flex min-w-0 items-center gap-2">
      <span
        className={cn("size-2 shrink-0 rounded-full", dotClass)}
        title={statusLabel}
        aria-hidden
      />
      <div className="flex min-w-0 flex-1 items-baseline gap-1.5 truncate">
        <span className="shrink-0 font-medium text-foreground text-sm" title={role}>
          {role}
        </span>
        {run.task ? (
          <>
            <span className="shrink-0 text-muted-foreground text-xs" aria-hidden>
              ·
            </span>
            <span
              className="min-w-0 truncate text-muted-foreground text-xs"
              title={run.task}
            >
              {run.task}
            </span>
          </>
        ) : null}
        {run.kind !== "agent" ? (
          <span className="shrink-0 text-muted-foreground text-xs">({run.kind})</span>
        ) : null}
      </div>
    </div>
  );

  const shellClass = cn(
    "min-w-0 w-full rounded-lg border px-2.5 py-1.5 text-left outline-none transition-colors",
    active
      ? "border-primary/40 bg-primary/10 ring-2 ring-primary/25"
      : "border-border/60 bg-card hover:bg-accent/60",
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
          className={cn(shellClass, "focus-visible:ring-2 focus-visible:ring-ring")}
        >
          {card}
        </button>
      ) : (
        <div className={shellClass}>{card}</div>
      )}
      {children.length > 0 && (
        <ul className="mt-1 ml-3 space-y-1 border-muted-foreground/25 border-l pl-2">
          {children.map((child) => (
            <TeamNode
              key={child.id}
              run={child}
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
