import { Centered, EmptyHint, InlineError } from "@/components/files/parts";
import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { notifyActionError } from "@/lib/toast";
import {
  type WorkspaceTrashEntry,
  listTrash,
  restoreTrash,
} from "@/services/workspace";
import { wsListTrash, wsRestoreTrash } from "@/services/workspaces";
import { Loader2, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * AgentCore/trash list + one-click restore.
 *
 * Three flavours, one panel: cloud addressed by conversation (chat side dock),
 * cloud addressed by workspace id (文件页), and the desktop no-OS-trash fallback.
 * They differ only in **which IO** they call and **what the header honestly says**
 * — the OS recycle bin is a separate track that this panel never claims to list.
 */

interface TrashLoad {
  entries: WorkspaceTrashEntry[];
  /** Server-reported retention; the local fallback zone has no such policy. */
  retentionDays?: number;
}

function TrashPanel({
  hint,
  emptyTitle,
  emptyHint,
  load,
  restore,
}: {
  /** Header copy; receives the retention the last load reported. */
  hint: (retentionDays: number) => string;
  emptyTitle: string;
  emptyHint: string;
  load: () => Promise<TrashLoad>;
  restore: (entryId: string) => Promise<void>;
}) {
  const [entries, setEntries] = useState<WorkspaceTrashEntry[] | null>(null);
  const [retentionDays, setRetentionDays] = useState(30);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  // 切会话 / ws / root 不关层：丢弃在途 list，还原闭包绑到列出这批的身份。
  const genRef = useRef(0);
  const restoreForListRef = useRef(restore);

  const reload = useCallback(async () => {
    const gen = ++genRef.current;
    const restoreForThisLoad = restore;
    setLoading(true);
    setError(false);
    try {
      const res = await load();
      if (gen !== genRef.current) return;
      restoreForListRef.current = restoreForThisLoad;
      setEntries(res.entries);
      if (res.retentionDays !== undefined) setRetentionDays(res.retentionDays);
    } catch {
      if (gen !== genRef.current) return;
      setError(true);
    } finally {
      if (gen === genRef.current) setLoading(false);
    }
  }, [load, restore]);

  // load 身份变了：先清列表，避免 A 的条目配上 B 的 restore。
  // biome-ignore lint/correctness/useExhaustiveDependencies: deps 故意含 reload，切换时跑 cleanup bump gen
  useEffect(() => {
    setEntries(null);
    setError(false);
    void reload();
    return () => {
      genRef.current += 1;
    };
  }, [reload]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border px-3 py-2">
        <p className="min-w-0 flex-1 text-xs text-muted-foreground">
          {hint(retentionDays)}
        </p>
        <SimpleTooltip label="刷新">
          <IconButton
            disabled={loading}
            onClick={() => void reload()}
            aria-label="刷新"
          >
            {loading ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <RefreshCw size={14} />
            )}
          </IconButton>
        </SimpleTooltip>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3 pt-2">
        {error ? (
          <InlineError onRetry={() => void reload()} />
        ) : entries === null ? (
          <Centered>
            <Loader2
              size={18}
              className="animate-spin text-muted-foreground/50"
            />
          </Centered>
        ) : entries.length === 0 ? (
          <EmptyHint
            inline
            icon={<Trash2 size={22} className="text-muted-foreground/40" />}
            title={emptyTitle}
            hint={emptyHint}
          />
        ) : (
          <ul className="space-y-1">
            {entries.map((e) => (
              <TrashRow
                key={e.entryId}
                entry={e}
                onRestore={() => restoreForListRef.current(e.entryId)}
                onRestored={() => void reload()}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

const CLOUD_EMPTY_TITLE = "软删区为空";
const CLOUD_EMPTY_HINT = "云端可逆删除会进入此处；可用「还原」放回原路径。";

const cloudHint = (retentionDays: number) =>
  `工作区软删区（保留约 ${retentionDays} 天）。本地系统回收站删除不在此列，请在本机回收站恢复。`;

/**
 * Cloud AgentCore/trash for a conversation's workspace (chat side dock).
 *
 * Local OS recycle-bin deletes are a separate track — never listed here.
 * Local no-OS-trash fallback uses desktop IPC (see LocalTrashSection).
 */
export function TrashSection({ conversationId }: { conversationId: string }) {
  const load = useCallback(() => listTrash(conversationId), [conversationId]);
  const restore = useCallback(
    (entryId: string) => restoreTrash(conversationId, entryId),
    [conversationId],
  );
  return (
    <TrashPanel
      hint={cloudHint}
      emptyTitle={CLOUD_EMPTY_TITLE}
      emptyHint={CLOUD_EMPTY_HINT}
      load={load}
      restore={restore}
    />
  );
}

/**
 * Cloud AgentCore/trash addressed by workspace id — the 文件页 twin of
 * {@link TrashSection}. Same zone, same copy; the hub just has no conversation
 * to address it with. Cloud, non-`shared:` workspaces only (the server refuses
 * the rest), so the caller gates the entry point.
 */
export function WorkspaceTrashSection({ wsId }: { wsId: string }) {
  const load = useCallback(() => wsListTrash(wsId), [wsId]);
  const restore = useCallback(
    (entryId: string) => wsRestoreTrash(wsId, entryId),
    [wsId],
  );
  return (
    <TrashPanel
      hint={cloudHint}
      emptyTitle={CLOUD_EMPTY_TITLE}
      emptyHint={CLOUD_EMPTY_HINT}
      load={load}
      restore={restore}
    />
  );
}

/**
 * Local AgentCore/trash (no-OS-trash fallback). OS shell.trashItem is not listed.
 */
export function LocalTrashSection({ rootId }: { rootId: string }) {
  const load = useCallback(async (): Promise<TrashLoad> => {
    const res = await window.fsApi.listWorkspaceTrash(rootId);
    if (!res.ok) throw new Error(res.reason);
    return {
      entries: res.data.map((e) => ({
        entryId: e.entryId,
        originalPath: e.originalPath,
        name: e.name,
        isDir: e.isDir,
        deletedAt: e.deletedAt,
      })),
    };
  }, [rootId]);
  const restore = useCallback(
    async (entryId: string) => {
      const res = await window.fsApi.restoreWorkspaceTrash(rootId, entryId);
      if (!res.ok) throw new Error(res.reason);
    },
    [rootId],
  );
  return (
    <TrashPanel
      hint={() =>
        "仅列出工作区软删兜底（无系统回收站时）。经系统回收站删除的文件请在本机回收站恢复——产品不提供一键还原。"
      }
      emptyTitle="工作区软删区为空"
      emptyHint="默认删除进系统回收站；仅当无系统回收站时才会落入此处。"
      load={load}
      restore={restore}
    />
  );
}

function TrashRow({
  entry,
  onRestore,
  onRestored,
}: {
  entry: WorkspaceTrashEntry;
  onRestore: () => Promise<void>;
  onRestored: () => void;
}) {
  const [busy, setBusy] = useState(false);

  const restore = async () => {
    if (busy) return;
    if (!window.confirm(`还原「${entry.originalPath}」到原路径？`)) return;
    setBusy(true);
    try {
      await onRestore();
      onRestored();
    } catch (e) {
      notifyActionError("还原失败", e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="rounded-lg border border-border px-2.5 py-2">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium">{entry.name}</div>
          <div className="truncate text-xs text-muted-foreground">
            {entry.originalPath}
            {entry.isDir ? "（目录）" : ""}
          </div>
        </div>
        <SimpleTooltip label="还原到原路径">
          <IconButton
            disabled={busy}
            onClick={() => void restore()}
            aria-label="还原"
          >
            {busy ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <RotateCcw size={14} />
            )}
          </IconButton>
        </SimpleTooltip>
      </div>
    </li>
  );
}
