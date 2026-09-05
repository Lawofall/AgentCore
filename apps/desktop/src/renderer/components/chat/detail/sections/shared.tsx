import type { RunNode } from "@/stores/execution";

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
