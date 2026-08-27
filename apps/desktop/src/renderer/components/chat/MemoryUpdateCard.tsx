import {
  MemoryUpdateItemRow,
  formatMemoryTime,
  memoryScopeOverview,
  visibleMemoryUpdateItems,
} from "@/components/memory/MemoryUpdateItemRow";
import { Card } from "@/components/ui";
import { countPillMuted, statusCardChrome } from "@/components/ui/tone-presets";
import { getConversations } from "@/hooks/useConversations";
import { queryClient } from "@/lib/queryClient";
import { cn } from "@/lib/utils";
import {
  MEMORY_DISPUTED_LINES_KEY,
  MEMORY_UPDATES_KEY,
} from "@/services/memory";
import {
  memoryLeafTabName,
  parseProjectMemoryFolderId,
} from "@/services/sources/memorySource";
import type { MemoryUpdate } from "@/stores/conversation";
import { useConversationStore } from "@/stores/conversation";
import { usePersistentDisclosure } from "@/stores/disclosure";
import { Brain, ChevronDown, ChevronRight, NotebookPen } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { memoryAnchorTime } from "./messageTimeline";

/** 本场摘要超过此长度（或含换行）默认两行截断，可展开全文（对齐 ConclusionHero）。 */
export const EPISODIC_SUMMARY_CLAMP_CHARS = 60;

/** 情景层是巩固素材，不是已经生效的现行记忆。 */
export const EPISODIC_CARD_HEADING = "本场摘记";

/**
 * Memory-write notice on the conversation timeline (two-layer memory).
 *
 * Bordered muted Card shell (摘要 / 记忆 only) — other timeline metadata stays ghost.
 * Expand / navigate behavior unchanged.
 *
 * - ``episodic``: light tip — session digest was filed for later consolidation.
 * - ``semantic``: expandable diff — what changed in 偏好 / 画像 / 主题.
 * - ``quota``: the always pool is full — the summary says so and the rows name every
 *   entry that could not be written plus the ones holding the pool (审计 CTX-A2).
 */
export function MemoryUpdateCard({ update }: { update: MemoryUpdate }) {
  const navigate = useNavigate();
  const chrome = statusCardChrome("muted");
  const [open, setOpen] = usePersistentDisclosure(`memory:${update.id}`, false);
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const conversationFolderId =
    getConversations().find((c) => c.id === conversationId)?.folderId ?? null;
  // 与卡片在时间线上的落点同一个时刻，否则卡片会显示得比它下方的消息还晚。
  const timeLabel = formatMemoryTime(memoryAnchorTime(update));

  const isEpisodic = update.kind === "episodic";
  if (isEpisodic) {
    const tip = (update.summary ?? "").trim();
    if (!tip) return null;
    const long =
      tip.length > EPISODIC_SUMMARY_CLAMP_CHARS || tip.includes("\n");
    return (
      <Card
        className={`animate-task-card-enter ${chrome.border} ${chrome.surface}`}
      >
        <div className="flex w-full items-start gap-2 px-3 py-2 text-left">
          <NotebookPen
            size={16}
            className={`mt-0.5 shrink-0 ${chrome.accent}`}
          />
          <div className="min-w-0 flex-1">
            {long ? (
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                className="flex w-full items-center gap-2 text-left"
                data-testid="episodic-summary-toggle"
              >
                <span className={`text-xs font-medium ${chrome.accent}`}>
                  {EPISODIC_CARD_HEADING}
                </span>
                <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                  {timeLabel}
                </span>
                {open ? (
                  <ChevronDown
                    size={14}
                    className="shrink-0 text-muted-foreground"
                  />
                ) : (
                  <ChevronRight
                    size={14}
                    className="shrink-0 text-muted-foreground"
                  />
                )}
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <span className={`text-xs font-medium ${chrome.accent}`}>
                  {EPISODIC_CARD_HEADING}
                </span>
                <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                  {timeLabel}
                </span>
              </div>
            )}
            <p
              className={cn(
                "mt-0.5 text-xs text-muted-foreground",
                !open && long && "line-clamp-2",
              )}
            >
              {tip}
            </p>
          </div>
        </div>
      </Card>
    );
  }

  const items = visibleMemoryUpdateItems(update.items);
  if (items.length === 0 && !(update.summary ?? "").trim()) return null;

  const openLeaf = (target: string, projectId?: string | null) => {
    const folderId = parseProjectMemoryFolderId(target) ?? projectId ?? null;
    navigate("/files", {
      state: {
        openMemoryLeaf: {
          path: target,
          name: memoryLeafTabName(target),
          ...(projectId ? { projectId } : {}),
        },
        ...(folderId ? { focusWsId: `folder:${folderId}` } : {}),
      },
    });
  };

  // This card is the main way in to「这条不对」, but what shows the result — 记忆动态 and its
  // 已移走的记忆 list — lives in another route with its own cache. Without this the user
  // rejects a line here, goes looking for it there, and finds nothing.
  const memoryChanged = () => {
    void queryClient.invalidateQueries({ queryKey: MEMORY_UPDATES_KEY });
    void queryClient.invalidateQueries({ queryKey: MEMORY_DISPUTED_LINES_KEY });
  };

  const hasAnyTarget = items.some((it) => it.target);
  const scopeOverview = memoryScopeOverview(items);
  // A quota card is not a change log: its summary IS the message (什么没写进来、为什么),
  // and the rows below it name the entries.
  const isQuota = update.kind === "quota";
  const title = isQuota
    ? (update.summary ?? "常驻条目已满")
    : items.length > 0
      ? scopeOverview
        ? `记忆已更新 · ${scopeOverview}`
        : "记忆已更新"
      : (update.summary ?? "记忆已整理");

  // Prefer conversation project; else any project id already on the items (for
  // 「移到全局」 / naming when the card was produced in a project chat).
  const projectFolderId =
    conversationFolderId || items.find((it) => it.projectId)?.projectId || null;

  return (
    <Card
      className={`animate-task-card-enter ${chrome.border} ${chrome.surface}`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <Brain size={16} className={`shrink-0 ${chrome.accent}`} />
        <span
          className={`min-w-0 truncate text-xs font-medium ${chrome.accent}`}
        >
          {title}
        </span>
        {items.length > 0 && (
          <span className={countPillMuted}>{items.length} 项</span>
        )}
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">
          {timeLabel}
        </span>
        {items.length > 0 ? (
          open ? (
            <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight
              size={14}
              className="shrink-0 text-muted-foreground"
            />
          )
        ) : null}
      </button>
      {open && items.length > 0 && (
        <div className="px-3 pb-3">
          <ul className="space-y-0.5">
            {items.map((item, i) => (
              <MemoryUpdateItemRow
                key={`${item.action}:${item.file}:${item.section}:${i}`}
                item={item}
                onOpenLeaf={openLeaf}
                projectFolderId={projectFolderId}
                onMemoryChanged={memoryChanged}
              />
            ))}
          </ul>
          {!hasAnyTarget && (
            <div className="mt-2 flex justify-end">
              <a
                href="#/files"
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                在「全局设定」中查看
                <ChevronRight size={13} />
              </a>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
