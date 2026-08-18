import { getTokens } from "@/api/client";
import {
  type ConversationSummary,
  type ConversationTrash,
  type DeletedConversationSummary,
  type FolderGroup,
  deleteConversation,
  listConversationTrash,
  listConversations,
  listConversationsGrouped,
  renameConversation,
  restoreConversation,
  setConversationArchived,
  setConversationPinned,
} from "@/api/conversations";
import { type SearchSection, search } from "@/api/search";
import { ShareConversationSheet } from "@/components/ShareConversationSheet";
import {
  ActionSheet,
  ConfirmDialog,
  RenameDialog,
  SearchResults,
  timeLabel,
} from "@/components/conversations";
import {
  useAiAttention,
  useConversationAwaitingAttention,
} from "@/lib/aiAttention";
import { useConversationCloudRunning } from "@/lib/aiTurnActivity";
import { folderWorkspaceId } from "@/lib/cloudFolder";
import {
  DELETE_CONVERSATION_LABEL,
  deleteConversationConfirmLabel,
} from "@/lib/conversationDeleteCopy";
import {
  isDrawerGroupExpanded,
  readDrawerGroupExpand,
  writeDrawerGroupExpand,
} from "@/lib/conversationDrawerExpand";
import { buildConversationDrawerRail } from "@/lib/conversationDrawerRail";
import {
  getConversationListArchived,
  insertRestored,
  patchConversation,
  removeConversation,
  replaceArchived,
  replaceGrouped,
  useConversationListArchived,
  useConversationListGrouped,
} from "@/lib/conversationListCache";
import { retentionRemainingLabel } from "@/lib/conversationTrash";
import {
  ChevronDown,
  ChevronRight,
  Cloud,
  Folder,
  Plus,
  SquarePen,
} from "lucide-react";
// 历史对话抽屉 (手机端对话页重设计 · 抽屉式直聊).
//
// The chat page is now「开盖即聊」(a fresh draft on the 对话 tab); the conversation history
// that used to be the landing list lives here, as a left slide-in drawer opened from the chat
// header's ☰. Mirrors the desktop sidebar's recent-conversations + the industry pattern
// (ChatGPT/Claude 左抽屉历史). Hosts the same management surface the old list page had —
// 搜索 / 已归档 / 最近删除 / 行内 置顶·重命名·分享·归档·删除 — reusing the
// shared primitives in conversations.tsx. 最近删除 is a drawer view (no
// /conversations route); it has no folder half and no 彻底删除.
//
// Live list is folder-grouped (`listConversationsGrouped`) then cut into the
// 方案 C rail (置顶 / 组 / 裸聊); archived stays a flat `listConversations(true)`.
// Trash fetches `listConversationTrash` only when that view is open. Cache is
// the list truth: open / 切已归档 still fetch then replace; rename / pin /
// delete / undo / trash-restore patch the cache. Stream title_generated /
// message_start (and fulfill bump) update the open drawer. Group expand persist
// survives close; 等你 on a rail row force-expands without writing back.
// Picking a conversation routes to /c/:id and closes; trash rows cannot open.
// ✎ starts a new draft (routes to /, the draft home) and closes. Cloud group
// 「＋」lands on / with draftFolder state.
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

const UNDO_MS = 8000;

type DrawerListView = "live" | "archived" | "trash";

export function ConversationDrawer({
  open,
  onClose,
  onOpen,
  activeId,
}: {
  open: boolean;
  onClose: () => void;
  onOpen: () => void;
  /** The conversation open in the chat behind the drawer — highlighted in the list. */
  activeId?: string;
}) {
  const navigate = useNavigate();
  const grouped = useConversationListGrouped();
  const items = useConversationListArchived();
  const attention = useAiAttention();
  const [expandMap, setExpandMap] = useState(readDrawerGroupExpand);
  const [view, setView] = useState<DrawerListView>("live");
  const [trash, setTrash] = useState<ConversationTrash | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Search (跨会话搜索) — independent of the list view; an empty query shows the list.
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchSection[] | null>(null);
  const [searching, setSearching] = useState(false);
  // Per-row management surfaces.
  const [menuFor, setMenuFor] = useState<ConversationSummary | null>(null);
  const [renaming, setRenaming] = useState<ConversationSummary | null>(null);
  const [deleting, setDeleting] = useState<ConversationSummary | null>(null);
  const [sharing, setSharing] = useState<ConversationSummary | null>(null);
  const [undo, setUndo] = useState<{
    id: string;
    title: string;
  } | null>(null);
  const undoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Drag gestures: left-edge swipe opens, leftward swipe on the open panel closes. The panel
  // follows the finger (`drag.x` = live translateX), then CSS settles on release. `open` and
  // the callbacks are read via refs so the touch listeners can attach exactly once.
  const edgeRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const [drag, setDrag] = useState<{ x: number; frac: number } | null>(null);
  const dragXRef = useRef(0);
  const openRef = useRef(open);
  openRef.current = open;
  const onOpenRef = useRef(onOpen);
  onOpenRef.current = onOpen;
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Lazy fetch: only when open (a closed drawer costs nothing), and refetch when
  // the list view changes. Trash is fetched only while that view is open. A
  // cleared token routes to login (mirrors the chat page gate). Cache stays the
  // render truth — do not null it at fetch start.
  useEffect(() => {
    if (!open) return;
    setError(null);
    if (view === "trash") {
      listConversationTrash()
        .then(setTrash)
        .catch((e) => {
          if (!getTokens()) {
            navigate("/login", { replace: true });
            return;
          }
          setError(e instanceof Error ? e.message : "加载最近删除失败");
          setTrash({ items: [], retention_days: 0, total: 0 });
        });
      return;
    }
    const req =
      view === "archived"
        ? listConversations(true).then(replaceArchived)
        : listConversationsGrouped().then(replaceGrouped);
    req.catch((e) => {
      if (!getTokens()) {
        navigate("/login", { replace: true });
        return;
      }
      setError(e instanceof Error ? e.message : "加载会话列表失败");
      if (view === "archived") replaceArchived([]);
      else replaceGrouped({ folders: [], ungrouped: [] });
    });
  }, [open, view, navigate]);

  function clearUndo() {
    if (undoTimerRef.current != null) {
      clearTimeout(undoTimerRef.current);
      undoTimerRef.current = null;
    }
    setUndo(null);
  }

  // Reset transient surfaces when the drawer closes, so reopening is clean.
  useEffect(() => {
    if (open) return;
    setQuery("");
    setMenuFor(null);
    setRenaming(null);
    setDeleting(null);
    setSharing(null);
    if (undoTimerRef.current != null) {
      clearTimeout(undoTimerRef.current);
      undoTimerRef.current = null;
    }
    setUndo(null);
  }, [open]);

  useEffect(() => {
    return () => {
      if (undoTimerRef.current != null) {
        clearTimeout(undoTimerRef.current);
      }
    };
  }, []);

  // Touch-drag open/close (attached once; reads state via refs). A drag from the left-edge
  // strip pulls the panel in; a leftward drag on the open panel pushes it out. Direction-
  // locked after 8px so a vertical list scroll or a row tap is never hijacked. touchmove is
  // non-passive so we can preventDefault once we've claimed a horizontal drag.
  useEffect(() => {
    const edge = edgeRef.current;
    const panel = panelRef.current;
    if (!edge || !panel) return;
    type Gesture = {
      startX: number;
      startY: number;
      w: number;
      mode: "pending" | "h" | "v";
      opening: boolean;
    };
    let g: Gesture | null = null;

    const begin = (opening: boolean, x: number, y: number) => {
      const w = panel.offsetWidth || Math.min(window.innerWidth * 0.84, 360);
      g = { startX: x, startY: y, w, mode: "pending", opening };
    };
    const onEdgeStart = (e: TouchEvent) => {
      if (openRef.current) return;
      const t = e.touches[0];
      if (t) begin(true, t.clientX, t.clientY);
    };
    const onPanelStart = (e: TouchEvent) => {
      if (!openRef.current) return;
      const t = e.touches[0];
      if (t) begin(false, t.clientX, t.clientY);
    };
    const onMove = (e: TouchEvent) => {
      if (!g) return;
      const t = e.touches[0];
      if (!t) return;
      const dx = t.clientX - g.startX;
      const dy = t.clientY - g.startY;
      if (g.mode === "pending") {
        if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
        g.mode = Math.abs(dx) > Math.abs(dy) ? "h" : "v";
      }
      if (g.mode !== "h") return;
      if (g.opening && dx <= 0) return; // open needs a rightward pull
      if (!g.opening && dx >= 0) return; // close needs a leftward pull
      e.preventDefault(); // we own this gesture now — suppress scroll / text selection
      const x = g.opening
        ? Math.max(-g.w, Math.min(0, -g.w + dx))
        : Math.max(-g.w, Math.min(0, dx));
      dragXRef.current = x;
      setDrag({ x, frac: (x + g.w) / g.w });
    };
    const onEnd = () => {
      if (!g) return;
      const done = g;
      g = null;
      if (done.mode !== "h") return;
      const x = dragXRef.current;
      setDrag(null);
      // Past the halfway line wins; otherwise CSS snaps back to the controlled state.
      const revealed = x > -done.w / 2;
      if (done.opening && revealed) onOpenRef.current();
      else if (!done.opening && !revealed) onCloseRef.current();
    };

    edge.addEventListener("touchstart", onEdgeStart, { passive: true });
    panel.addEventListener("touchstart", onPanelStart, { passive: true });
    window.addEventListener("touchmove", onMove, { passive: false });
    window.addEventListener("touchend", onEnd, { passive: true });
    window.addEventListener("touchcancel", onEnd, { passive: true });
    return () => {
      edge.removeEventListener("touchstart", onEdgeStart);
      panel.removeEventListener("touchstart", onPanelStart);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onEnd);
      window.removeEventListener("touchcancel", onEnd);
    };
  }, []);

  // Debounced keyword search; `active` drops a stale response if the query moved on.
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (!q) {
      setResults(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    let active = true;
    const handle = setTimeout(() => {
      search(q)
        .then((s) => active && setResults(s))
        .catch(() => active && setResults([]))
        .finally(() => active && setSearching(false));
    }, 250);
    return () => {
      active = false;
      clearTimeout(handle);
    };
  }, [query, open]);

  function openConversation(id: string | null) {
    if (!id) return;
    navigate(`/c/${id}`);
    onClose();
  }

  function newChat() {
    navigate("/", { state: {} });
    onClose();
  }

  function toggleGroup(id: string, displayed: boolean) {
    writeDrawerGroupExpand(id, !displayed);
    setExpandMap(readDrawerGroupExpand());
  }

  function openFolderFiles(folder: FolderGroup) {
    navigate(`/files/${encodeURIComponent(folderWorkspaceId(folder.id))}`, {
      state: { name: folder.name },
    });
  }

  function newInFolder(folder: FolderGroup) {
    navigate("/", {
      state: { draftFolderId: folder.id, draftFolderName: folder.name },
    });
    onClose();
  }

  async function doRename(conv: ConversationSummary, title: string) {
    setBusy(true);
    setError(null);
    try {
      const updated = await renameConversation(conv.id, title);
      patchConversation(conv.id, { title: updated.title });
      setRenaming(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "重命名失败");
    } finally {
      setBusy(false);
    }
  }

  async function doArchiveToggle(conv: ConversationSummary) {
    setBusy(true);
    setError(null);
    try {
      await setConversationArchived(conv.id, !conv.archived);
      removeConversation(conv.id);
      setMenuFor(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function doPinToggle(conv: ConversationSummary) {
    setBusy(true);
    setError(null);
    try {
      const updated = await setConversationPinned(conv.id, !conv.pinned);
      patchConversation(conv.id, { pinned: updated.pinned });
      setMenuFor(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  function armUndo(conv: ConversationSummary) {
    if (undoTimerRef.current != null) {
      clearTimeout(undoTimerRef.current);
      undoTimerRef.current = null;
    }
    setUndo({ id: conv.id, title: conv.title || "新对话" });
    undoTimerRef.current = setTimeout(() => {
      undoTimerRef.current = null;
      setUndo(null);
    }, UNDO_MS);
  }

  async function doDelete(conv: ConversationSummary) {
    setBusy(true);
    setError(null);
    try {
      await deleteConversation(conv.id);
      removeConversation(conv.id);
      setDeleting(null);
      armUndo(conv);
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  function applyRestored(restored: ConversationSummary) {
    if (restored.archived) {
      const current = getConversationListArchived();
      replaceArchived([
        restored,
        ...(current ?? []).filter((x) => x.id !== restored.id),
      ]);
    } else {
      insertRestored(restored);
    }
  }

  async function doUndo() {
    if (!undo) return;
    const id = undo.id;
    setBusy(true);
    setError(null);
    try {
      const restored = await restoreConversation(id);
      applyRestored(restored);
      clearUndo();
    } catch (e) {
      setError(e instanceof Error ? e.message : "恢复失败");
    } finally {
      setBusy(false);
    }
  }

  async function doRestoreTrash(row: DeletedConversationSummary) {
    setBusy(true);
    setError(null);
    try {
      const restored = await restoreConversation(row.id);
      applyRestored(restored);
      setTrash((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.filter((x) => x.id !== row.id),
              total: Math.max(0, prev.total - 1),
            }
          : prev,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "恢复失败");
    } finally {
      setBusy(false);
    }
  }

  const searchMode = query.trim().length > 0;
  const loading =
    !error &&
    (view === "archived"
      ? items === null
      : view === "trash"
        ? trash === null
        : grouped === null);
  const empty =
    view === "archived"
      ? items?.length === 0
      : view === "trash"
        ? trash?.items.length === 0
        : Boolean(
            grouped &&
              grouped.ungrouped.length === 0 &&
              grouped.folders.every((f) => f.conversations.length === 0),
          );
  const emptyHint =
    view === "archived"
      ? "没有已归档的对话。"
      : view === "trash"
        ? trash && trash.retention_days > 0
          ? `最近删除是空的。删除的对话会在这里保留 ${trash.retention_days} 天，其间随时可以恢复。`
          : "最近删除是空的。"
        : "还没有对话，点 ✎ 开始。";
  const archivedView = view === "archived";
  const rail = grouped ? buildConversationDrawerRail(grouped) : null;
  const requiredIds = new Set(attention.map((e) => e.conversationId));

  return (
    <div
      className={`drawer-root${open ? " open" : ""}${drag ? " dragging" : ""}`}
      aria-hidden={!open && !drag}
    >
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: backdrop is a supplementary tap-to-close */}
      <div
        className="drawer-backdrop"
        style={drag ? { opacity: drag.frac } : undefined}
        onClick={onClose}
      />
      {/* Left-edge strip that captures a swipe-to-open while the drawer is closed. */}
      <div className="drawer-edge" ref={edgeRef} aria-hidden />
      <aside
        className="drawer"
        ref={panelRef}
        style={drag ? { transform: `translateX(${drag.x}px)` } : undefined}
        // biome-ignore lint/a11y/useSemanticElements: swipe/drag drawer panel; migrating to native <dialog> (showModal/::backdrop) is a separate a11y task
        role="dialog"
        aria-modal={open}
        aria-label="对话历史"
      >
        <header className="bar">
          <div className="drawer-bar-lead">
            {view !== "live" && (
              <button
                type="button"
                className="link drawer-back"
                aria-label="返回对话"
                onClick={() => setView("live")}
              >
                ←
              </button>
            )}
            <span className="drawer-bar-heading">
              {view === "archived"
                ? "已归档"
                : view === "trash"
                  ? "最近删除"
                  : "对话历史"}
            </span>
          </div>
          <div className="bar-right">
            {view !== "archived" && (
              <button
                type="button"
                className="link"
                onClick={() => setView("archived")}
              >
                已归档
              </button>
            )}
            {view !== "trash" && (
              <button
                type="button"
                className="link"
                onClick={() => setView("trash")}
              >
                最近删除
              </button>
            )}
            <button
              type="button"
              className="link icon-btn"
              aria-label="新对话"
              onClick={newChat}
            >
              <SquarePen size={20} />
            </button>
          </div>
        </header>

        <div className="search">
          <input
            className="search-input"
            placeholder="搜索对话和消息…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              type="button"
              className="search-clear"
              aria-label="清除"
              onClick={() => setQuery("")}
            >
              ✕
            </button>
          )}
        </div>

        {searchMode ? (
          <SearchResults
            sections={results}
            searching={searching}
            onOpen={openConversation}
          />
        ) : (
          <div className="list">
            {loading && <p className="muted hint">加载中…</p>}
            {empty && <p className="muted hint">{emptyHint}</p>}
            {view === "trash"
              ? trash?.items.map((row) => (
                  <TrashConversationRow
                    key={row.id}
                    row={row}
                    busy={busy}
                    onRestore={() => void doRestoreTrash(row)}
                  />
                ))
              : archivedView
                ? items?.map((c) => (
                    <ConversationRow
                      key={c.id}
                      conv={c}
                      active={c.id === activeId}
                      onOpen={() => openConversation(c.id)}
                      onMenu={() => setMenuFor(c)}
                    />
                  ))
                : rail && (
                    <>
                      {rail.pinned.length > 0 && (
                        <div className="drawer-rail-zone">
                          {rail.pinned.map((c) => (
                            <ConversationRow
                              key={c.id}
                              conv={c}
                              active={c.id === activeId}
                              onOpen={() => openConversation(c.id)}
                              onMenu={() => setMenuFor(c)}
                            />
                          ))}
                        </div>
                      )}
                      {rail.groups.length > 0 && (
                        <div className="drawer-rail-zone">
                          {rail.groups.map((folder) => {
                            const hasRequired = folder.conversations.some((c) =>
                              requiredIds.has(c.id),
                            );
                            const expanded = isDrawerGroupExpanded({
                              stored: expandMap[folder.id],
                              hasRequired,
                            });
                            return (
                              <FolderGroupBlock
                                key={folder.id}
                                folder={folder}
                                expanded={expanded}
                                activeId={activeId}
                                onToggle={() =>
                                  toggleGroup(folder.id, expanded)
                                }
                                onOpenFiles={() => openFolderFiles(folder)}
                                onNewInFolder={() => newInFolder(folder)}
                                onOpenConv={openConversation}
                                onMenu={setMenuFor}
                              />
                            );
                          })}
                        </div>
                      )}
                      {rail.bare.length > 0 && (
                        <div className="drawer-rail-zone">
                          {rail.bare.map((c) => (
                            <ConversationRow
                              key={c.id}
                              conv={c}
                              active={c.id === activeId}
                              onOpen={() => openConversation(c.id)}
                              onMenu={() => setMenuFor(c)}
                            />
                          ))}
                        </div>
                      )}
                    </>
                  )}
          </div>
        )}

        {undo && (
          <output className="drawer-undo">
            <span>已删除「{undo.title}」</span>
            <button
              type="button"
              className="drawer-undo-action"
              disabled={busy}
              onClick={() => void doUndo()}
            >
              撤销
            </button>
          </output>
        )}
        {error && <div className="error bar">{error}</div>}
      </aside>

      {menuFor && (
        <ActionSheet
          conv={menuFor}
          archivedView={archivedView}
          onClose={() => setMenuFor(null)}
          onRename={() => {
            const c = menuFor;
            setMenuFor(null);
            setRenaming(c);
          }}
          onPin={() => void doPinToggle(menuFor)}
          onShare={
            view === "live"
              ? () => {
                  const c = menuFor;
                  setMenuFor(null);
                  setSharing(c);
                }
              : undefined
          }
          onArchive={() => void doArchiveToggle(menuFor)}
          onDelete={() => {
            const c = menuFor;
            setMenuFor(null);
            setDeleting(c);
          }}
        />
      )}

      {sharing && (
        <ShareConversationSheet
          conversationId={sharing.id}
          title={sharing.title}
          onClose={() => setSharing(null)}
        />
      )}

      {renaming && (
        <RenameDialog
          conv={renaming}
          busy={busy}
          onClose={() => setRenaming(null)}
          onSave={(title) => void doRename(renaming, title)}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title={DELETE_CONVERSATION_LABEL}
          message={`删除「${deleting.title || "新对话"}」？${deleteConversationConfirmLabel()}`}
          confirmLabel={DELETE_CONVERSATION_LABEL}
          busy={busy}
          onCancel={() => setDeleting(null)}
          onConfirm={() => void doDelete(deleting)}
        />
      )}
    </div>
  );
}

function TrashConversationRow({
  row,
  busy,
  onRestore,
}: {
  row: DeletedConversationSummary;
  busy: boolean;
  onRestore: () => void;
}) {
  const remain = retentionRemainingLabel(row.purge_at);
  return (
    <div className="conv-row">
      <div className="conv conv-trash">
        <span className="conv-title">{row.title || "新对话"}</span>
        <span className="conv-meta">
          {row.message_count} 条{remain ? ` · ${remain}` : ""}
        </span>
      </div>
      <button
        type="button"
        className="conv-restore"
        disabled={busy}
        onClick={onRestore}
      >
        恢复
      </button>
    </div>
  );
}

function FolderGroupBlock({
  folder,
  expanded,
  activeId,
  onToggle,
  onOpenFiles,
  onNewInFolder,
  onOpenConv,
  onMenu,
}: {
  folder: FolderGroup;
  expanded: boolean;
  activeId?: string;
  onToggle: () => void;
  onOpenFiles: () => void;
  onNewInFolder: () => void;
  onOpenConv: (id: string | null) => void;
  onMenu: (c: ConversationSummary) => void;
}) {
  const cloud = folder.mode === "cloud";
  return (
    <div className="conv-group">
      <div className="conv-group-head">
        <button
          type="button"
          className="conv-group-toggle"
          aria-expanded={expanded}
          aria-label={expanded ? `收起${folder.name}` : `展开${folder.name}`}
          onClick={onToggle}
        >
          {expanded ? (
            <ChevronDown size={16} aria-hidden />
          ) : (
            <ChevronRight size={16} aria-hidden />
          )}
        </button>
        <span className="conv-group-mode" aria-hidden>
          {cloud ? <Cloud size={16} /> : <Folder size={16} />}
        </span>
        {cloud ? (
          <button
            type="button"
            className="conv-group-head-main"
            onClick={onOpenFiles}
          >
            <span className="conv-group-name">{folder.name}</span>
          </button>
        ) : (
          <button
            type="button"
            className="conv-group-head-main"
            onClick={onToggle}
          >
            <span className="conv-group-name">{folder.name}</span>
            <span className="conv-group-sub">请在桌面端打开</span>
          </button>
        )}
        {cloud && (
          <button
            type="button"
            className="conv-group-new"
            aria-label="在此新开"
            onClick={onNewInFolder}
          >
            <Plus size={18} aria-hidden />
          </button>
        )}
      </div>
      {expanded && (
        <div className="conv-group-body">
          {folder.conversations.map((c) => (
            <ConversationRow
              key={c.id}
              conv={c}
              active={c.id === activeId}
              onOpen={() => onOpenConv(c.id)}
              onMenu={() => onMenu(c)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * One history row + its「等你」/ running 灯.
 *
 * Title / rank come from the list cache; the lights can't — each row subscribes to
 * firehose `ai_attention` and account-level `ai_turn_activity` on its own. 等你光环
 * 压过 running；本机容器不吃云 running。Opening the conversation clears its
 * attention entries, so entering the chat turns that light off.
 */
function ConversationRow({
  conv,
  active,
  onOpen,
  onMenu,
}: {
  conv: ConversationSummary;
  active: boolean;
  onOpen: () => void;
  onMenu: () => void;
}) {
  const awaiting = useConversationAwaitingAttention(conv.id);
  const cloudRunning = useConversationCloudRunning(
    conv.id,
    conv.local_container_root_id,
  );
  const showRunning = !awaiting && cloudRunning;
  return (
    <div className="conv-row">
      <button
        type="button"
        className={`conv${active ? " conv-active" : ""}`}
        onClick={onOpen}
      >
        <span className="conv-line">
          <span className="conv-attention-slot">
            {awaiting && (
              <span
                className="conv-attention"
                role="img"
                aria-label="等你决策"
              />
            )}
            {showRunning && (
              <span className="conv-running" role="img" aria-label="执行中" />
            )}
          </span>
          <span className="conv-title">{conv.title || "新对话"}</span>
        </span>
        <span className="conv-meta">
          {conv.message_count} 条 · {timeLabel(conv.updated_at)}
        </span>
      </button>
      <button
        type="button"
        className="conv-actions"
        aria-label="更多操作"
        onClick={onMenu}
      >
        ⋯
      </button>
    </div>
  );
}
