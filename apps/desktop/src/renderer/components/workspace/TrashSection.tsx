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
import { Loader2, RotateCcw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * AgentCore/trash list + one-click restore.
 *
 * Three flavours, one panel: cloud addressed by conversation (chat side dock),
 * cloud addressed by workspace id (文件页), and the desktop no-OS-trash fallback.
 * They differ in which IO they call. The OS recycle bin is a separate track
 * this panel never lists; only the local fallback header says so.
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
  active = true,
}: {
  /** Local fallback header only. Cloud chrome already says 软删区. */
  hint?: string;
  emptyTitle: string;
  emptyHint: string | ((retentionDays: number) => string);
  load: () => Promise<TrashLoad>;
  restore: (entryId: string) => Promise<void>;
  /**
   * Files page tabs stay mounted while hidden. Flip false→true to silently
   * reload; omit / true for the chat overlay (remounts on open).
   */
  active?: boolean;
}) {
  const [entries, setEntries] = useState<WorkspaceTrashEntry[] | null>(null);
  const [retentionDays, setRetentionDays] = useState(30);
  const [error, setError] = useState(false);
  // 切会话 / ws / root 不关层：丢弃在途 list，还原闭包绑到列出这批的身份。
  const genRef = useRef(0);
  const restoreForListRef = useRef(restore);
  const activeRef = useRef(active);
  activeRef.current = active;

  const reload = useCallback(async () => {
    const gen = ++genRef.current;
    const restoreForThisLoad = restore;
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
    }
  }, [load, restore]);

  // load 身份变了：先清列表，避免 A 的条目配上 B 的 restore。
  useEffect(() => {
    setEntries(null);
    setError(false);
    if (activeRef.current === false) return;
    void reload();
    return () => {
      genRef.current += 1;
    };
  }, [reload]);

  // 文件页 tab 从 hidden 切回：静默重拉，不清空现有列表。
  const prevActiveRef = useRef(active);
  useEffect(() => {
    const wasHidden = prevActiveRef.current === false;
    prevActiveRef.current = active;
    if (active !== false && wasHidden) void reload();
  }, [active, reload]);

  const resolvedEmptyHint =
    typeof emptyHint === "function" ? emptyHint(retentionDays) : emptyHint;

  return (
    <div className="flex h-full flex-col">
      {hint ? (
        <div className="flex shrink-0 items-center border-b border-border px-3 py-2">
          <p className="min-w-0 flex-1 text-xs text-muted-foreground">{hint}</p>
        </div>
      ) : null}

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
            hint={resolvedEmptyHint}
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
const cloudEmptyHint = (retentionDays: number) =>
  `云端可逆删除会进入此处；可用「还原」放回原路径。约 ${retentionDays} 天后自动清除。`;

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
      emptyTitle={CLOUD_EMPTY_TITLE}
      emptyHint={cloudEmptyHint}
      load={load}
      restore={restore}
    />
  );
}

/**
 * Cloud AgentCore/trash addressed by workspace id — the 文件页 twin of
 * {@link TrashSection}. Same zone; the hub just has no conversation to address
 * it with. Cloud `folder:` / `conv:` workspaces only (the server refuses local),
 * so the caller gates the entry point.
 */
export function WorkspaceTrashSection({
  wsId,
  active = true,
}: {
  wsId: string;
  /** Files page keeps the tab mounted while hidden. */
  active?: boolean;
}) {
  const load = useCallback(() => wsListTrash(wsId), [wsId]);
  const restore = useCallback(
    (entryId: string) => wsRestoreTrash(wsId, entryId),
    [wsId],
  );
  return (
    <TrashPanel
      emptyTitle={CLOUD_EMPTY_TITLE}
      emptyHint={cloudEmptyHint}
      load={load}
      restore={restore}
      active={active}
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
      hint="仅列出工作区软删兜底（无系统回收站时）。经系统回收站删除的文件请在本机回收站恢复——产品不提供一键还原。"
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
