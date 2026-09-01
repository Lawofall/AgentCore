import {
  type RunNode,
  type RunStatus,
  runPhaseLabel,
  runStatusLabel,
  toolLabel,
} from "@/stores/execution";

export function Section({
  title,
  action,
  children,
}: {
  title: string;
  /** Optional right-aligned header control (e.g. the 版本链's 对比 deep-link). */
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-4 last:mb-0">
      <div className="mb-1 flex items-center gap-2">
        <h3 className="flex-1 text-xs font-medium text-muted-foreground">
          {title}
        </h3>
        {action}
      </div>
      {children}
    </section>
  );
}

export function StatusBadge({
  status,
  phase,
  phaseTool,
}: {
  status: string;
  phase?: RunNode["phase"];
  phaseTool?: RunNode["phaseTool"];
}) {
  const styles: Record<string, string> = {
    pending: "bg-muted text-muted-foreground",
    running: "bg-primary/10 text-primary",
    completed: "bg-success/10 text-success",
    failed: "bg-destructive/10 text-destructive",
    cancelled: "bg-muted text-muted-foreground",
    skipped: "bg-muted text-muted-foreground",
  };
  const phaseText =
    status === "running" ? runPhaseLabel(phase, phaseTool, toolLabel) : null;
  const label =
    phaseText ??
    (status in styles ? runStatusLabel(status as RunStatus) : status);
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${styles[status] ?? ""}`}
    >
      {label}
    </span>
  );
}

const STATUS_DOT: Record<string, string> = {
  pending: "bg-muted-foreground/30",
  running: "bg-primary",
  completed: "bg-success",
  failed: "bg-destructive",
  cancelled: "bg-muted-foreground/30",
  skipped: "bg-muted-foreground/30",
};

export function RunStatusDot({ status }: { status: RunNode["status"] }) {
  return (
    <span
      className={`size-2 shrink-0 rounded-full ${STATUS_DOT[status] ?? "bg-muted-foreground/30"}`}
    />
  );
}

export function MetricRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
      <span
        className={`text-right text-xs tabular-nums text-foreground ${mono ? "font-mono" : ""}`}
      >
        {value}
      </span>
    </div>
  );
}
