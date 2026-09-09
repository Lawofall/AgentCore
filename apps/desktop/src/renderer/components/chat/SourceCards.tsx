import { Button } from "@/components/ui";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { statusPillInline } from "@/components/ui/tone-presets";
import type { CitationDisplayMap } from "@/lib/citationDisplayMap";
import { cleanSourceTitle } from "@/lib/citations";
import { ledgerDateLabel } from "@/lib/evidenceLedger";
import { usePersistentDisclosure } from "@/stores/disclosure";
import type { Citation, TurnEvidenceLedgerEntry } from "@/types/events";
import { ChevronDown, ChevronUp, Globe, Info } from "lucide-react";
import { Favicon } from "./Favicon";
import { SourceTooltip } from "./SourcePreview";

/**
 * Source cards under an assistant reply: pages the deliverable actually cited
 * this turn (P2: backend `citations` event = cited subset; uncited hits stay on
 * the evidence ledger). Each card opens its URL in the system browser — the
 * Electron main process routes target=_blank through shell.openExternal.
 *
 * Layout follows the Perplexity / ChatGPT pattern. Collapsed: a single row of
 * compact favicon pills (index + favicon + domain) with a matching overflow pill
 * that stacks the hidden sources' favicons, so a source-heavy answer stays one
 * tidy row; the page title lives in the hover preview and the expanded list.
 * Expanded: a borderless vertical list with a leading index, favicon, title,
 * domain and snippet per source.
 *
 * Display numbers come from {@link CitationDisplayMap} (first-appearance order
 * in the reply body); chips and cards share the same map.
 */
const COLLAPSED_COUNT = 3;
/** How many favicons to stack as a preview inside the overflow chip. */
const STACK_PREVIEW = 3;

function sourceWasRead(
  citation: Citation,
  entry: TurnEvidenceLedgerEntry | null | undefined,
): boolean {
  return Boolean(entry?.deep_read ?? citation.deep_read);
}

/** Positive-only: search-snippet cites stay silent; fetched pages mark 已读. */
function SourceReadBadge({ read }: { read: boolean }) {
  if (!read) return null;
  return (
    <span
      className={`inline-flex shrink-0 items-center ${statusPillInline.success}`}
      title="团队已读取该页全文"
    >
      已读
    </span>
  );
}

export function SourceCards({
  citations,
  displayMap,
  turnKey,
  evidenceLedger,
}: {
  citations: Citation[];
  /** Shared renumbering with inline chips; when omitted, pool order + 1-based. */
  displayMap?: CitationDisplayMap | null;
  /** 回合作用域（= messageId）：给了才把「展开全部」跨卸载/刷新记住。 */
  turnKey?: string;
  /** 回合调研台账：来源卡 id 溯源面板（query / deep_read / registrant）。 */
  evidenceLedger?: TurnEvidenceLedgerEntry[] | null;
}) {
  const [expanded, setExpanded] = usePersistentDisclosure(
    turnKey ? `${turnKey}:sources` : null,
    false,
  );

  if (citations.length === 0) return null;

  const rows =
    displayMap?.rows ??
    citations.map((_, i) => ({
      poolIndex: i,
      display: i + 1,
      cited: false,
    }));

  const referencedDisplay = displayMap?.referencedDisplay;
  const hasRefs = !!referencedDisplay && referencedDisplay.size > 0;
  const citedCount = referencedDisplay?.size ?? 0;

  const hidden = rows.length - COLLAPSED_COUNT;
  const collapsed = rows.slice(0, COLLAPSED_COUNT);
  const stack = rows.slice(COLLAPSED_COUNT, COLLAPSED_COUNT + STACK_PREVIEW);

  const ledgerById = new Map<string, TurnEvidenceLedgerEntry>();
  for (const e of evidenceLedger ?? []) {
    if (e?.id) ledgerById.set(e.id, e);
  }

  // Dim sources the answer retrieved but never cited inline, so the cited ones
  // stand out; hover restores full opacity so muted sources stay inspectable.
  const dimClass = (cited: boolean) =>
    hasRefs && !cited ? "opacity-55 transition-opacity hover:opacity-100" : "";

  return (
    <div className="mt-3">
      {hidden > 0 ? (
        <Button
          variant="ghost"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="mb-1.5 h-auto gap-1.5 px-0 py-0 text-xs text-muted-foreground hover:bg-transparent hover:text-foreground"
        >
          <span className="flex items-center gap-1.5">
            <Globe size={14} className="shrink-0" />
            <span>来源 {citations.length}</span>
            {hasRefs && (
              <span className="text-muted-foreground/70">
                · {citedCount} 条被引用
              </span>
            )}
            {expanded ? (
              <ChevronUp size={14} className="ml-0.5" />
            ) : (
              <ChevronDown size={14} className="ml-0.5" />
            )}
          </span>
        </Button>
      ) : (
        <div className="mb-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Globe size={14} className="shrink-0" />
          <span>来源 {citations.length}</span>
          {hasRefs && (
            <span className="text-muted-foreground/70">
              · {citedCount} 条被引用
            </span>
          )}
        </div>
      )}
      {expanded ? (
        // Expanded: a vertical list with each source's snippet inline, like the
        // ChatGPT sources panel. Snippet is shown directly, so no hover preview.
        // Height is capped with internal scroll so a source-heavy answer (up to
        // 24) doesn't stretch the message; the 收起 button stays outside the
        // scroll area so it's always reachable.
        <div>
          <div className="flex max-h-96 flex-col gap-1.5 overflow-y-auto pr-1">
            {rows.map(({ poolIndex, display, cited }) => {
              const c = citations[poolIndex];
              if (!c) return null;
              const entry = c.id ? (ledgerById.get(c.id) ?? null) : null;
              const read = sourceWasRead(c, entry);
              return (
                <div
                  key={`${c.url}-${display}`}
                  className={`flex items-start gap-2.5 rounded-lg px-2.5 py-2 transition-colors hover:bg-accent ${dimClass(cited)}`}
                >
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`来源 ${display}：${cleanSourceTitle(c.title) || c.site || c.url}${read ? "（已读）" : ""}`}
                    className="flex min-w-0 flex-1 items-start gap-2.5"
                  >
                    <span className="mt-0.5 w-5 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                      {display}
                    </span>
                    <Favicon
                      site={c.site}
                      title={c.title}
                      size={18}
                      className="mt-0.5"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="block min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                          {cleanSourceTitle(c.title) || c.site || c.url}
                        </span>
                        <SourceReadBadge read={read} />
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
                  <SourceLedgerTrace citation={c} entry={entry} />
                </div>
              );
            })}
          </div>
          {hidden > 0 && (
            <Button
              variant="neutral"
              onClick={() => setExpanded(false)}
              className="mt-1.5 inline-flex w-fit border border-border bg-card text-muted-foreground hover:text-foreground"
              icon={<ChevronUp size={14} />}
            >
              收起
            </Button>
          )}
        </div>
      ) : (
        // Collapsed: a single row of compact favicon pills (index + favicon +
        // domain); hover reveals the title + snippet preview.
        <div className="flex flex-wrap items-center gap-1.5">
          {collapsed.map(({ poolIndex, display, cited }) => {
            const c = citations[poolIndex];
            if (!c) return null;
            const entry = c.id ? (ledgerById.get(c.id) ?? null) : null;
            const read = sourceWasRead(c, entry);
            return (
              <SourceTooltip
                key={`${c.url}-${display}`}
                citation={c}
                index={display}
              >
                <a
                  href={c.url}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={`来源 ${display}：${cleanSourceTitle(c.title) || c.site || c.url}${read ? "（已读）" : ""}`}
                  className={`flex items-center gap-1.5 rounded-full border border-border bg-card py-1 pl-2 pr-2.5 transition-colors hover:bg-accent ${dimClass(cited)}`}
                >
                  <span className="tabular-nums text-xs text-muted-foreground">
                    {display}
                  </span>
                  <Favicon site={c.site} title={c.title} size={16} />
                  <span className="max-w-[140px] truncate text-xs text-foreground">
                    {c.site || c.url}
                  </span>
                  <SourceReadBadge read={read} />
                </a>
              </SourceTooltip>
            );
          })}
          {hidden > 0 && (
            <Button
              variant="neutral"
              onClick={() => setExpanded(true)}
              className="h-auto gap-1.5 rounded-full border border-border bg-card py-1 pl-2 pr-2.5 text-xs text-muted-foreground hover:text-foreground"
            >
              <span className="flex items-center gap-1.5">
                <span className="flex items-center -space-x-1.5">
                  {stack.map(({ poolIndex, display }) => {
                    const c = citations[poolIndex];
                    if (!c) return null;
                    return (
                      <Favicon
                        key={`${c.url}-stack-${display}`}
                        site={c.site}
                        title={c.title}
                        size={16}
                        className="ring-2 ring-card"
                      />
                    );
                  })}
                </span>
                <span className="tabular-nums">+{hidden}</span>
              </span>
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

/** 来源卡 id 溯源面板：优先台账条目，其次 Citation 加宽字段；legacy 无字段则隐藏。 */
function SourceLedgerTrace({
  citation,
  entry,
}: {
  citation: Citation;
  entry: TurnEvidenceLedgerEntry | null;
}) {
  const id = entry?.id || citation.id;
  if (!id) return null;
  const query = (entry?.query ?? citation.query ?? "").trim();
  const deepRead = sourceWasRead(citation, entry);
  const registrant = (entry?.registrant ?? citation.registrant ?? "").trim();
  const date = entry?.date ?? citation.date;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
          className="inline-flex shrink-0 items-center justify-center rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label={`查看来源台账 ${id}`}
        >
          <Info size={13} aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-72 space-y-1.5 p-3 text-sm"
        align="end"
        side="top"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="truncate text-xs font-medium tabular-nums text-foreground">
          {id}
        </div>
        <div className="text-xs text-muted-foreground">
          {ledgerDateLabel(date)}
        </div>
        {query ? (
          <div className="text-xs">
            <span className="text-muted-foreground">检索 </span>
            <span className="text-foreground">{query}</span>
          </div>
        ) : null}
        <div className="text-xs">
          <span className="text-muted-foreground">已读 </span>
          <span className="text-foreground">{deepRead ? "是" : "否"}</span>
        </div>
        {registrant ? (
          <div className="text-xs">
            <span className="text-muted-foreground">登记者 </span>
            <span className="text-foreground">{registrant}</span>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
