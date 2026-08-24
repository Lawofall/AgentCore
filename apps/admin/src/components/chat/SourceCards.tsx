import type { NormalizedCitation } from "@/components/chat/chatTurn";
import { cn } from "@/lib/utils";
import { ChevronDown, ChevronRight, Globe } from "lucide-react";
import { useState } from "react";

const PREVIEW_COUNT = 3;

function pillLabel(c: NormalizedCitation): string {
  return c.title || c.site || c.url || "来源";
}

/**
 * Desktop-like source row: titles stay visible as compact numbered pills; snippets
 * stay behind the expand control so a source-heavy turn does not inflate
 * the reading column.
 */
export function SourceCards({
  citations,
}: {
  citations: NormalizedCitation[];
}) {
  const [open, setOpen] = useState(false);
  if (citations.length === 0) return null;
  const preview = citations.slice(0, PREVIEW_COUNT);
  const extra = citations.length - preview.length;

  return (
    <section aria-label="来源" className="min-w-0 max-w-full space-y-1.5">
      <button
        type="button"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="inline-flex items-center gap-1.5 rounded-lg text-muted-foreground text-xs outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Globe size={14} className="shrink-0" aria-hidden />
        <span>
          来源 {citations.length}
          {extra > 0 && !open ? ` · 还有 ${extra} 个` : ""}
        </span>
        {open ? (
          <ChevronDown size={14} className="shrink-0" aria-hidden />
        ) : (
          <ChevronRight size={14} className="shrink-0" aria-hidden />
        )}
      </button>
      {!open && (
        <ul className="flex flex-wrap items-center gap-1.5">
          {preview.map((c, i) => (
            <li key={c.id || `${c.url}-${i}`}>
              <span
                className="inline-flex max-w-[11rem] items-center gap-1.5 truncate rounded-full border border-border bg-card py-1 pl-2 pr-2.5 text-xs"
                title={pillLabel(c)}
              >
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {i + 1}
                </span>
                <span className="min-w-0 truncate text-foreground">
                  {pillLabel(c)}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {open && (
        <ul className="space-y-1.5">
          {citations.map((c, i) => (
            <li
              key={c.id || `${c.url}-${i}-full`}
              className="min-w-0 rounded-lg border border-border bg-card px-3 py-2 text-sm"
            >
              <div className="flex min-w-0 items-start gap-2">
                <span className="mt-0.5 w-4 shrink-0 text-right text-muted-foreground text-xs tabular-nums">
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p
                    className="font-medium text-foreground"
                    title={pillLabel(c)}
                  >
                    {pillLabel(c)}
                  </p>
                  {c.snippet ? (
                    <p className="mt-1 line-clamp-3 break-words text-muted-foreground text-xs leading-relaxed">
                      {c.snippet}
                    </p>
                  ) : null}
                  {c.url ? (
                    <a
                      href={c.url}
                      target="_blank"
                      rel="noreferrer"
                      className={cn(
                        "mt-1 block truncate text-primary text-xs",
                        !c.snippet && "mt-0.5",
                      )}
                    >
                      {c.url}
                    </a>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
