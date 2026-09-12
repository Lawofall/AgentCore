import {
  formatMemoryTime,
  visibleMemoryUpdateItems,
} from "@/components/memory/MemoryUpdateItemRow";
import { statusPillInline } from "@/components/ui/tone-presets";
import {
  type MemoryUpdateFeedEntry,
  listMemoryUpdates,
} from "@/services/memory";
import { useEffect, useState } from "react";

type MemoryCoreKind = "preferences" | "profile";

const FILE_LABEL: Record<MemoryCoreKind, string> = {
  preferences: "偏好",
  profile: "画像",
};

const ACTION_META: Record<
  "add" | "update" | "remove",
  { label: string; tone: "success" | "primary" | "muted" }
> = {
  add: { label: "新增", tone: "success" },
  update: { label: "更新", tone: "primary" },
  remove: { label: "移除", tone: "muted" },
};

type RecentWrite = {
  key: string;
  action: keyof typeof ACTION_META;
  summary: string;
  createdAt: string;
};

function matchesKind(file: string, kind: MemoryCoreKind): boolean {
  const label = FILE_LABEL[kind];
  return file === label || file === `${label}.md`;
}

function writeAction(
  action: MemoryUpdateFeedEntry["items"][number]["action"],
): RecentWrite["action"] | null {
  if (action === "add" || action === "update" || action === "remove") {
    return action;
  }
  return null;
}

function collectRecentWrites(
  entries: MemoryUpdateFeedEntry[],
  memoryKind: MemoryCoreKind,
): RecentWrite[] {
  const rows: RecentWrite[] = [];
  for (const entry of entries) {
    for (const [i, item] of visibleMemoryUpdateItems(entry.items).entries()) {
      if (!matchesKind(item.file, memoryKind)) continue;
      const action = writeAction(item.action);
      if (!action) continue;
      const summary = (item.content || item.section).trim();
      if (!summary) continue;
      rows.push({
        key: `${entry.id}:${action}:${item.file}:${item.section}:${i}`,
        action,
        summary,
        createdAt: entry.createdAt,
      });
      if (rows.length >= 3) return rows;
    }
  }
  return rows;
}

/**
 * Compact recap of the latest AI writes into one always-injected memory leaf
 * (偏好.md / 画像.md). Renders nothing when the feed has no matching items or
 * the request fails — the host page already owns empty/error chrome.
 */
export function MemoryRecentWrites({
  memoryKind,
}: {
  memoryKind: MemoryCoreKind;
}) {
  const [rows, setRows] = useState<RecentWrite[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    void listMemoryUpdates()
      .then((entries) => {
        if (cancelled) return;
        const picked = collectRecentWrites(entries, memoryKind);
        setRows(picked.length > 0 ? picked : null);
      })
      .catch(() => {
        if (cancelled) return;
        setRows(null);
      });
    return () => {
      cancelled = true;
    };
  }, [memoryKind]);

  if (!rows?.length) return null;

  return (
    <section
      data-testid="memory-recent-writes"
      aria-label="最近写入"
      className="mx-3 mb-3 shrink-0 rounded-xl border border-border bg-card/60 p-3"
    >
      <div className="text-xs font-medium text-muted-foreground">最近写入</div>
      <ul className="mt-1.5 space-y-1.5">
        {rows.map((row) => {
          const meta = ACTION_META[row.action];
          return (
            <li key={row.key} className="flex items-start gap-2">
              <span className={`shrink-0 ${statusPillInline[meta.tone]}`}>
                {meta.label}
              </span>
              <p className="min-w-0 flex-1 whitespace-pre-wrap break-words text-sm text-foreground">
                {row.summary}
              </p>
              <time
                dateTime={row.createdAt}
                className="shrink-0 text-xs text-muted-foreground"
              >
                {formatMemoryTime(row.createdAt)}
              </time>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
