import { FileWarning } from "lucide-react";
import {
  type CodeDiagnosticItem,
  type CodeDiagnosticsDisplay,
  codeDiagnosticsErrorCount,
} from "./codeDiagnostics";

/**
 * Inner-loop type diagnostics card — neutral / warning chrome, never the outer-loop
 * outer-loop incomplete banner and never fault-red melt styling.
 */
export function CodeDiagnosticsResult({
  display,
}: {
  display: CodeDiagnosticsDisplay;
}) {
  if (display.status === "unavailable") {
    return (
      <div className="mt-1 overflow-hidden rounded-lg border border-border">
        <Header />
        <div className="bg-muted/30 px-2.5 py-2 text-xs text-muted-foreground">
          {display.reason?.trim() || "类型诊断暂不可用"}
        </div>
      </div>
    );
  }

  const errors = display.diagnostics.filter((d) => d.severity === "error");
  const n = codeDiagnosticsErrorCount(display);

  return (
    <div className="mt-1 overflow-hidden rounded-lg border border-border">
      <Header badge={n > 0 ? `${n} 个错误` : undefined} />
      {errors.length === 0 ? (
        <div className="bg-muted/30 px-2.5 py-2 text-xs text-muted-foreground">
          未发现类型错误
        </div>
      ) : (
        <ul className="max-h-72 list-none space-y-1 overflow-auto bg-muted/30 px-2.5 py-2">
          {errors.map((d, i) => (
            <DiagnosticRow
              // biome-ignore lint/suspicious/noArrayIndexKey: positional diagnostic list
              key={i}
              item={d}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function Header({ badge }: { badge?: string }) {
  return (
    <div className="flex items-center gap-2 border-border/60 border-b bg-muted/40 px-2.5 py-1 text-xs">
      <FileWarning size={12} className="shrink-0 text-muted-foreground" />
      <span className="text-muted-foreground">类型诊断</span>
      {badge && (
        <span className="ml-auto shrink-0 tabular-nums text-warning">
          {badge}
        </span>
      )}
    </div>
  );
}

function DiagnosticRow({ item }: { item: CodeDiagnosticItem }) {
  const loc = `${item.path}:${item.line}`;
  return (
    <li className="font-mono text-xs leading-relaxed text-foreground/90">
      <span className="text-muted-foreground">{loc}</span>
      <span className="text-muted-foreground"> · </span>
      <span>{item.message}</span>
      {item.code && (
        <span className="ml-1 text-muted-foreground/70">({item.code})</span>
      )}
    </li>
  );
}
