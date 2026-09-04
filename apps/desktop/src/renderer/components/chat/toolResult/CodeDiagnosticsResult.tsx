import type {
  CodeDiagnosticItem,
  CodeDiagnosticsDisplay,
} from "./codeDiagnostics";

/**
 * Inner-loop type diagnostics expand body. Count / unavailable reason live on
 * the ToolLine; this card is the error list (or unavailable reason) only.
 */
export function CodeDiagnosticsResult({
  display,
}: {
  display: CodeDiagnosticsDisplay;
}) {
  if (display.status === "unavailable") {
    return (
      <div className="mt-1 overflow-hidden rounded-lg border border-border">
        <div className="bg-muted/30 px-2.5 py-2 text-xs text-muted-foreground">
          {display.reason?.trim() || "类型诊断暂不可用"}
        </div>
      </div>
    );
  }

  const errors = display.diagnostics.filter((d) => d.severity === "error");
  if (errors.length === 0) return null;

  return (
    <div className="mt-1 overflow-hidden rounded-lg border border-border">
      <ul className="max-h-72 list-none space-y-1 overflow-auto bg-muted/30 px-2.5 py-2">
        {errors.map((d, i) => (
          <DiagnosticRow
            // biome-ignore lint/suspicious/noArrayIndexKey: positional diagnostic list
            key={i}
            item={d}
          />
        ))}
      </ul>
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
