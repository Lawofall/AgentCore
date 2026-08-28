import { Centered, EmptyHint, InlineError } from "@/components/files/parts";
import { DisputedLinesSection } from "@/components/memory/DisputedLinesSection";
import {
  MemoryUpdateItemRow,
  formatMemoryTime,
  visibleMemoryUpdateItems,
} from "@/components/memory/MemoryUpdateItemRow";
import {
  MEMORY_DISPUTED_LINES_KEY,
  MEMORY_UPDATES_KEY,
  listMemoryUpdates,
} from "@/services/memory";
import { memoryLeafTabName } from "@/services/sources/memorySource";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, History, Loader2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

/**
 * 记忆动态 — files-page「最近更新」view (记忆更新对话内可见, §1.6,
 * hybrid 方向 B 的家).
 *
 * The in-conversation card ({@link MemoryUpdateCard}) answers「这次对话 AI 记了什么」;
 * this view answers「AI 最近都学了什么」— the write side of memory is per-user long-term
 * data, so its natural home is ONE chronological stream cutting across every conversation.
 * Each entry is one offline-consolidation pass: a time + the applied changes (reusing the
 * same {@link MemoryUpdateItemRow} as the card, so a change reads identically in both) with
 * per-leaf deep-links, plus a jump back to the source conversation.
 *
 * Opened as a synthetic tab in the {@link FileWorkbench} (`MEMORY_UPDATES_PATH`); leaf
 * deep-links open a tab in the SAME workbench (no navigation) via {@link onOpenLeaf}.
 */
export function MemoryUpdatesView({
  onOpenLeaf,
}: {
  /** Open a memory leaf as a tab in this workbench (synthetic leaf path + display name +
   * optional projectId fallback when path does not encode a folder id). */
  onOpenLeaf: (path: string, name: string, projectId?: string | null) => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const updates = useQuery({
    queryKey: MEMORY_UPDATES_KEY,
    queryFn: () => listMemoryUpdates(),
    staleTime: 30_000,
  });
  const entries = updates.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-6">
        <History size={16} className="shrink-0 text-muted-foreground" />
        <span className="text-sm font-medium text-foreground">记忆动态</span>
        <span className="text-xs text-muted-foreground">
          AI 最近从各处对话里记下的内容
        </span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* Outside the empty branch below: a user may have rejected lines without ever
            having a consolidation pass listed here, and that is exactly when he most
            needs to find them again. */}
        <div className="mx-auto max-w-3xl px-6 pt-4 empty:hidden">
          <DisputedLinesSection />
        </div>
        {updates.isLoading ? (
          <Centered>
            <Loader2
              size={18}
              className="animate-spin text-muted-foreground/50"
            />
          </Centered>
        ) : updates.isError ? (
          <InlineError onRetry={() => void updates.refetch()} />
        ) : entries.length === 0 ? (
          <EmptyHint
            inline
            icon={<Brain size={26} className="text-muted-foreground/40" />}
            title="还没有记忆更新"
            hint="AI 会在对话后台整理长期记忆；记下新内容时，这里会按时间列出。"
          />
        ) : (
          <div className="mx-auto max-w-3xl space-y-3 px-6 py-4">
            {entries.map((entry) => (
              <section
                key={entry.id}
                className="rounded-xl border border-border bg-card/60 p-3"
              >
                <div className="flex items-center gap-2 px-1.5">
                  <span className="text-xs font-medium text-muted-foreground">
                    {formatMemoryTime(entry.createdAt)}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {entry.kind === "quota" ? "常驻已满" : "画像更新"}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      navigate(`/conversations/${entry.conversationId}`)
                    }
                    className="ml-auto shrink-0 text-xs text-muted-foreground hover:text-foreground hover:underline"
                  >
                    查看来源对话
                  </button>
                </div>
                <ul className="mt-1.5 space-y-0.5">
                  {entry.kind === "quota" && entry.summary ? (
                    <li className="px-1.5 pb-1 text-xs text-muted-foreground">
                      {entry.summary}
                    </li>
                  ) : null}
                  {visibleMemoryUpdateItems(entry.items).map((item, i) => (
                    <MemoryUpdateItemRow
                      key={`${item.action}:${item.file}:${item.section}:${i}`}
                      item={item}
                      projectFolderId={item.projectId}
                      onMemoryChanged={() => {
                        void updates.refetch();
                        // A row's「这条不对」lands in the rejected list too.
                        void queryClient.invalidateQueries({
                          queryKey: MEMORY_DISPUTED_LINES_KEY,
                        });
                      }}
                      onOpenLeaf={(target, projectId) =>
                        onOpenLeaf(target, memoryLeafTabName(target), projectId)
                      }
                    />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
