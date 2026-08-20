import { Badge } from "@/components/ui/Badge";
import type { NormalizedCitation } from "@/components/chat/chatTurn";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

export function SourceCards({
  citations,
}: {
  citations: NormalizedCitation[];
}) {
  const [open, setOpen] = useState(false);
  if (citations.length === 0) return null;
  return (
    <section aria-label="来源" className="min-w-0 max-w-full space-y-2">
      <button
        type="button"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring rounded-lg"
      >
        来源 {citations.length}
        {open ? (
          <ChevronDown size={14} className="shrink-0" aria-hidden />
        ) : (
          <ChevronRight size={14} className="shrink-0" aria-hidden />
        )}
      </button>
      {open && (
        <ul className="space-y-2">
          {citations.map((c, i) => (
            <li
              key={c.id || `${c.url}-${i}`}
              className="min-w-0 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span
                  className="min-w-0 truncate font-medium text-foreground"
                  title={c.title || c.url || "来源"}
                >
                  {c.title || c.url || "来源"}
                </span>
                {c.tier && <Badge className="shrink-0" tone="neutral">{c.tier}</Badge>}
                {c.site && (
                  <span className="min-w-0 truncate text-muted-foreground text-xs" title={c.site}>
                    {c.site}
                  </span>
                )}
              </div>
              {c.url && (
                <a
                  href={c.url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-0.5 block truncate text-xs text-primary"
                >
                  {c.url}
                </a>
              )}
              {c.snippet && (
                <p className="mt-1 line-clamp-2 break-words text-muted-foreground text-xs">
                  {c.snippet}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
