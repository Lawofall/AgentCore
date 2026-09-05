import { Badge, Button } from "@/components/ui";
import { cleanSourceTitle } from "@/lib/citations";
import { useStreamAwareDisclosure } from "@/stores/disclosure";
import type { Citation, ProcessStep, WebFetchDisplay } from "@/types/events";
import { ChevronDown, ChevronRight, Globe } from "lucide-react";
import { Favicon } from "./Favicon";
import { ThinkingDots } from "./message-bubble/Thinking";

type ToolStep = Extract<ProcessStep, { kind: "tool" }>;

function isWebFetchDisplay(d: unknown): d is WebFetchDisplay {
  if (!d) return false;
  const x = d as { url?: unknown; content?: unknown };
  return typeof x.url === "string" && typeof x.content === "string";
}

/** Aggregate source cards from each web_fetch step's display (never parse result JSON). */
function sourcesFromTools(tools: ToolStep[]): Citation[] {
  const out: Citation[] = [];
  for (const t of tools) {
    if (isWebFetchDisplay(t.display)) {
      out.push({
        url: t.display.url,
        title: t.display.title,
        site: t.display.site,
        snippet: t.display.snippet,
      });
      continue;
    }
    // Still running / no display yet — keep a slot from the call arg so the expanded list doesn't jump.
    const url =
      typeof t.arguments.url === "string" ? t.arguments.url.trim() : "";
    if (url) out.push({ url, title: "" });
  }
  return out;
}

/**
 * Merged view for a tool-group of ≥2 consecutive `web_fetch` calls: collapses to a bare
 *「Read page · N sources」header row (对齐工具组 / 思考过程的折叠态——折叠即收起细节，不再
 * 平铺来源 pills), expands into a SourceCards-aligned vertical list (index · favicon ·
 * title · domain · snippet). No inline page body — that stays on the single-`web_fetch`
 * card. Replaces ToolLineGroup's chevron so there is only one disclosure layer;
 * persistence reuses the same `${turnKey}:tgrp:${groupKey}` key.
 */
export function WebFetchSourceCollection({
  tools,
  isStreaming,
  turnKey,
  groupKey,
}: {
  tools: ToolStep[];
  isStreaming: boolean;
  turnKey?: string;
  groupKey?: string;
}) {
  const [expanded, toggleExpanded] = useStreamAwareDisclosure(
    turnKey != null && groupKey != null ? `${turnKey}:tgrp:${groupKey}` : null,
    isStreaming,
  );

  const citations = sourcesFromTools(tools);
  const errorCount = tools.reduce(
    (n, t) => n + (t.status === "error" ? 1 : 0),
    0,
  );
  const running = tools.some((t) => t.status === "running");
  const count = tools.length;
  const title = `Read page · ${count} source${count === 1 ? "" : "s"}`;

  return (
    <div>
      <Button
        variant="ghost"
        onClick={toggleExpanded}
        aria-expanded={expanded}
        className="mb-1.5 h-auto w-full justify-start gap-1.5 px-0 py-0 text-sm text-muted-foreground hover:bg-transparent hover:text-foreground"
      >
        <span className="flex items-center gap-1.5">
          {running ? (
            <ThinkingDots />
          ) : (
            <Globe size={14} className="shrink-0" />
          )}
          <span className="min-w-0 truncate text-left">{title}</span>
          {errorCount > 0 && (
            <Badge tone="destructive" className="shrink-0 font-normal">
              {errorCount} failed
            </Badge>
          )}
          {expanded ? (
            <ChevronDown size={14} className="shrink-0" />
          ) : (
            <ChevronRight size={14} className="shrink-0" />
          )}
        </span>
      </Button>

      {expanded && (
        <div className="flex max-h-96 flex-col gap-1.5 overflow-y-auto pr-1">
          {citations.map((c, i) => (
            <a
              key={`${c.url}-${i}`}
              href={c.url}
              target="_blank"
              rel="noreferrer"
              aria-label={`来源 ${i + 1}：${cleanSourceTitle(c.title) || c.site || c.url}`}
              className="flex items-start gap-2.5 rounded-lg px-2.5 py-2 transition-colors hover:bg-accent"
            >
              <span className="mt-0.5 w-5 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                {i + 1}
              </span>
              <Favicon
                site={c.site}
                title={c.title}
                size={18}
                className="mt-0.5"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-foreground">
                  {cleanSourceTitle(c.title) || c.site || c.url}
                </span>
                {c.site && (
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                    {c.site}
                  </span>
                )}
                {c.snippet && (
                  <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                    {c.snippet}
                  </p>
                )}
              </span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

/** True when a tool-group should render as a merged source collection. */
export function isWebFetchSourceGroup(tools: ToolStep[]): boolean {
  return tools.length >= 2 && tools.every((t) => t.tool_name === "web_fetch");
}
