import { countPillMuted, statusPillInline } from "@/components/ui/tone-presets";
import { getFolders } from "@/hooks/useFolders";
import { notifyActionError, notifyInfo } from "@/lib/toast";
import { ApiError } from "@/services/api";
import {
  type MemoryMoveDirection,
  type MemoryMoveKind,
  disputeMemoryLine,
  moveMemoryBullet,
  restoreMemoryLine,
} from "@/services/memory";
import type { MemoryUpdateItem } from "@/stores/conversation";
import { ChevronRight, Loader2 } from "lucide-react";
import { useState } from "react";

/**
 * One applied memory change (新增/更新/移除 + 目标叶子 + 正文), shared by the in-conversation
 * 「记忆已更新」card ({@link MemoryUpdateCard}) and the files-page「最近更新」feed
 * ({@link MemoryUpdatesView}) — one source of truth for how a change reads, so the two
 * surfaces never drift (Agent记忆与知识系统 §1.6).
 *
 * A row with a `target` is a button that deep-links to that exact memory leaf (the caller
 * decides HOW to open it — navigate to /files from the conversation, or open a tab directly
 * inside the editor); rows without a resolvable `target` render as plain text.
 *
 * Quota cards reuse these rows so「什么没写进来 / 谁占着配额」reads like any other memory
 * row and deep-links the same way; {@link visibleMemoryUpdateItems} hides the internal
 * fingerprint row.
 *
 * P2-a: folder-scope pill shows `本文件夹 · {名}` (falls back to「本文件夹」when the
 * folder name is unknown). P2-b: optional「移到本文件夹 / 移到全局」on add/update rows
 * when a current folder is known and the section allows the move.
 *
 *「这条不对」sits with the move actions at the top-right of the row (纠错通道·行级), as a
 * sibling of the open-leaf button so the two never nest. It belongs HERE rather than only
 * in the memory editor because this card is where the user meets the sentence — sending him
 * to a file to reject what he is already looking at is what made the entry-level channel a
 * choice between collateral damage and giving up. One click, no confirm dialog: the line he
 * sees is exactly the line that goes, and the toast carries 撤销.
 */

const ACTION_META: Record<
  MemoryUpdateItem["action"],
  { label: string; tone: "success" | "primary" | "muted" }
> = {
  add: { label: "新增", tone: "success" },
  update: { label: "更新", tone: "primary" },
  remove: { label: "移除", tone: "muted" },
  // Always-pool quota rows (审计 CTX-A2): what could NOT be written, and which entries
  // are currently holding the pool. Nothing was evicted — these name the trade-off.
  quota: { label: "配额", tone: "muted" },
  quota_denied: { label: "未写入", tone: "muted" },
  quota_holder: { label: "占用", tone: "muted" },
};

/** Quota rows report pool state; they are not applied changes and cannot be moved. */
const QUOTA_ACTIONS = new Set(["quota", "quota_denied", "quota_holder"]);

/**
 * Drop rows that exist only for the backend (the `quota` row carries the card's dedup
 * fingerprint — a hash the user must never see). Used for both rendering and counting so
 * the「N 项」pill matches what the card actually lists.
 */
export function visibleMemoryUpdateItems<T extends { action: string }>(
  items: readonly T[],
): T[] {
  return items.filter((it) => it.action !== "quota");
}

/** Resolve a folder id to its display name from the cached folder list. */
export function resolveProjectName(
  projectId: string | null | undefined,
): string | null {
  if (!projectId) return null;
  return getFolders().find((f) => f.id === projectId)?.name ?? null;
}

/** Scope pill label: `全局` / `本文件夹 · {名}` / `本文件夹` (name resolve failure). */
export function memoryScopePillLabel(
  scope: string,
  projectId?: string | null,
): string {
  if (scope !== "project") return "全局";
  const name = resolveProjectName(projectId);
  return name ? `本文件夹 · ${name}` : "本文件夹";
}

/**
 * Compact scope overview for a card title (e.g. `全局 + 本文件夹 · Foo`). Empty when
 * there are no items.
 */
export function memoryScopeOverview(
  items: ReadonlyArray<{ scope: string; projectId?: string | null }>,
): string {
  if (items.length === 0) return "";
  const parts: string[] = [];
  if (items.some((it) => it.scope !== "project")) parts.push("全局");
  const seen = new Set<string>();
  for (const it of items) {
    if (it.scope !== "project") continue;
    const key = it.projectId ?? "";
    if (seen.has(key)) continue;
    seen.add(key);
    parts.push(memoryScopePillLabel("project", it.projectId));
  }
  return parts.join(" + ");
}

function moveKindFromItem(item: MemoryUpdateItem): {
  kind: MemoryMoveKind;
  topicSlug: string | null;
} {
  if (item.file === "偏好") return { kind: "preferences", topicSlug: null };
  if (item.file.startsWith("主题·")) {
    return {
      kind: "topic",
      topicSlug: item.file.slice("主题·".length) || null,
    };
  }
  const m = /\/topics\/(.+)$/.exec(item.target ?? "");
  if (m) return { kind: "topic", topicSlug: m[1] };
  return { kind: "profile", topicSlug: null };
}

/** Whether this row may offer a move in ``direction`` (section invariants + content). */
export function canMoveMemoryItem(
  item: MemoryUpdateItem,
  direction: MemoryMoveDirection,
  projectFolderId: string | null | undefined,
): boolean {
  if (item.action === "remove") return false;
  if (QUOTA_ACTIONS.has(item.action)) return false;
  if (!(item.content ?? "").trim()) return false;
  if (!projectFolderId) return false;
  if (
    item.file === "偏好" ||
    item.section === "沟通偏好" ||
    item.section === "工作习惯"
  ) {
    return false;
  }
  if (direction === "to_project") {
    if (item.scope === "project") return false;
    if (item.section === "纠正记录") return false;
    return true;
  }
  if (item.scope !== "project") return false;
  if (item.section === "项目约束") return false;
  return true;
}

/**
 * Whether this row may be rejected line-by-line (纠错通道·行级).
 *
 * Wider than a move: no folder needed (global rows qualify) and 偏好 is fair game — the
 * scope invariants that block a move are about WHERE a line may live, not about whether
 * the user is allowed to say it is wrong. `remove` rows are already out of memory, and
 * quota rows report pool state rather than remembered content.
 */
export function canDisputeMemoryItem(item: MemoryUpdateItem): boolean {
  if (item.action === "remove") return false;
  if (QUOTA_ACTIONS.has(item.action)) return false;
  return Boolean((item.content ?? "").trim());
}

export function MemoryUpdateItemRow({
  item,
  onOpenLeaf,
  projectFolderId,
  onMemoryChanged,
}: {
  item: MemoryUpdateItem;
  onOpenLeaf: (target: string, projectId?: string | null) => void;
  /** Current folder — enables「移到本文件夹」for global rows. */
  projectFolderId?: string | null;
  /**
   * Fired after this row changed memory — a move, a rejection, or its undo. Every host
   * must pass it: the surfaces that show the result (记忆动态 and its 已移走的记忆 list)
   * are usually NOT the one the user clicked in, and stale caches make a landed change
   * look like it did nothing.
   */
  onMemoryChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const meta = ACTION_META[item.action];
  const leafLabel = item.section ? `${item.file} · ${item.section}` : item.file;
  const removed = item.action === "remove";
  const dimmed = removed || item.action === "quota_denied";
  const folderId = projectFolderId || item.projectId || null;
  const showToProject = canMoveMemoryItem(item, "to_project", folderId);
  const showToGlobal = canMoveMemoryItem(item, "to_global", folderId);

  const runMove = async (direction: MemoryMoveDirection) => {
    if (!folderId || busy) return;
    setBusy(true);
    try {
      const { kind, topicSlug } = moveKindFromItem(item);
      const result = await moveMemoryBullet({
        content: item.content,
        section: item.section || (kind === "topic" ? "要点" : ""),
        folderId,
        direction,
        kind,
        topicSlug,
      });
      if (result.conflict) {
        notifyInfo("记忆刚被更新，请刷新后再试搬层");
        return;
      }
      notifyInfo(direction === "to_project" ? "已移到本文件夹" : "已移到全局");
      onMemoryChanged?.();
    } catch (e) {
      notifyActionError(
        "搬层失败",
        e instanceof ApiError ? (e.serverMessage ?? e.message) : e,
      );
    } finally {
      setBusy(false);
    }
  };

  // Which layer this line actually lives in — a rejection targets its source, unlike a
  // move, whose folderId names the destination.
  const disputeFolderId =
    item.scope === "project"
      ? (item.projectId ?? projectFolderId ?? null)
      : null;

  const undoDispute = async (
    lineId: string,
    kind: MemoryMoveKind,
    topicSlug: string | null,
  ) => {
    try {
      await restoreMemoryLine({
        id: lineId,
        kind,
        topicSlug,
        folderId: disputeFolderId,
      });
      notifyInfo("已放回这条记忆");
      onMemoryChanged?.();
    } catch (e) {
      notifyActionError(
        "恢复失败",
        e instanceof ApiError ? (e.serverMessage ?? e.message) : e,
      );
    }
  };

  const runDispute = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const { kind, topicSlug } = moveKindFromItem(item);
      const result = await disputeMemoryLine({
        content: item.content,
        section: item.section || (kind === "topic" ? "要点" : ""),
        folderId: disputeFolderId,
        kind,
        topicSlug,
      });
      if (result.conflict) {
        notifyInfo("记忆刚被更新，请刷新后再试");
        return;
      }
      // Wording stays inside the honest boundary: the line stops being used, but nothing
      // here prevents the AI learning the same thing again from a later conversation.
      notifyInfo("这条不再用了", {
        description: "已从记忆里移走，同一条目的其他内容照常生效",
        action: result.lineId
          ? {
              label: "撤销",
              onClick: () => {
                void undoDispute(result.lineId, kind, topicSlug);
              },
            }
          : undefined,
      });
      onMemoryChanged?.();
    } catch (e) {
      notifyActionError(
        "操作失败",
        e instanceof ApiError ? (e.serverMessage ?? e.message) : e,
      );
    } finally {
      setBusy(false);
    }
  };

  const showDispute = canDisputeMemoryItem(item);

  const rowControls =
    showToProject || showToGlobal || showDispute ? (
      <div className="flex shrink-0 items-center gap-2 pt-0.5">
        {showToProject && (
          <button
            type="button"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              void runMove("to_project");
            }}
            className="whitespace-nowrap text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-50"
          >
            移到本文件夹
          </button>
        )}
        {showToGlobal && (
          <button
            type="button"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              void runMove("to_global");
            }}
            className="whitespace-nowrap text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-50"
          >
            移到全局
          </button>
        )}
        {showDispute && (
          <button
            type="button"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              void runDispute();
            }}
            title="把这句话从记忆里移走（可撤销）"
            className="whitespace-nowrap text-xs text-muted-foreground underline-offset-2 hover:text-destructive hover:underline disabled:opacity-50"
          >
            这条不对
          </button>
        )}
        {busy && (
          <Loader2 size={12} className="animate-spin text-muted-foreground" />
        )}
      </div>
    ) : null;

  const metaBlock = (
    <>
      <div className="flex min-w-0 items-center gap-1.5 text-xs">
        <span className="min-w-0 truncate font-medium text-foreground">
          {leafLabel}
        </span>
        <span className={countPillMuted}>
          {memoryScopePillLabel(item.scope, item.projectId)}
        </span>
      </div>
      {item.content && (
        <p
          className={`mt-0.5 whitespace-pre-wrap break-words text-sm ${
            dimmed ? "text-muted-foreground" : "text-foreground"
          } ${removed ? "line-through" : ""}`}
        >
          {item.content}
        </p>
      )}
    </>
  );

  const main = (
    <>
      <span className={`shrink-0 ${statusPillInline[meta.tone]}`}>
        {meta.label}
      </span>
      <div className="min-w-0 flex-1">{metaBlock}</div>
      {item.target ? (
        <ChevronRight
          size={14}
          className="mt-0.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
        />
      ) : null}
    </>
  );

  return (
    <li
      className={`flex items-start gap-2 px-1.5 py-1 ${
        item.target ? "rounded-lg hover:bg-accent/50" : ""
      }`}
    >
      {item.target ? (
        <button
          type="button"
          onClick={() => onOpenLeaf(item.target, item.projectId)}
          title={`在设定中打开${item.file}`}
          className="group flex min-w-0 flex-1 items-start gap-2 text-left"
        >
          {main}
        </button>
      ) : (
        <div className="flex min-w-0 flex-1 items-start gap-2">{main}</div>
      )}
      {rowControls}
    </li>
  );
}

/** Timestamp label shared by the memory card + feed (MM-DD HH:mm). */
export function formatMemoryTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes(),
  )}`;
}
