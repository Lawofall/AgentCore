import { FileDetail } from "@/components/files/FileDetail";
import { MemoryProfileSplitEditor } from "@/components/files/MemoryProfileSplitEditor";
import { MemoryUpdatesView } from "@/components/files/MemoryUpdatesView";
import type { FileSortBy } from "@/components/files/fileTreeTypes";
import { AgentCoreSection } from "@/components/files/fileWorkbench/AgentCoreSection";
import { DetailTabs } from "@/components/files/fileWorkbench/DetailTabs";
import {
  EntriesSection,
  type EntryOpenTarget,
} from "@/components/files/fileWorkbench/EntriesSection";
import { FileSortMenu } from "@/components/files/fileWorkbench/FileSortMenu";
import {
  type FolderRailHost,
  FolderRailNodes,
  FolderRailRow,
  folderWorkspaceFallback,
} from "@/components/files/fileWorkbench/FolderRailNodes";
import {
  LocalFoldersRailHeader,
  MyFilesRailHeader,
  SharedWithMeRailHeader,
} from "@/components/files/fileWorkbench/RailHeaders";
import { WorkspaceVersionsPanel } from "@/components/files/fileWorkbench/WorkspaceVersionsPanel";
import { createAndOpenScopeEntry } from "@/components/files/fileWorkbench/createScopeEntry";
import {
  type Tab,
  WS_TRASH_PATH,
  WS_VERSIONS_PATH,
  clampRail,
  folderIdOf,
  loadExpandedWs,
  loadFileSort,
  loadRailWidth,
  saveExpandedWs,
  saveFileSort,
  saveRailWidth,
  tabKey,
} from "@/components/files/fileWorkbench/storage";
import { EmptyHint, InlineError } from "@/components/files/parts";
import { PendingFolderInvites } from "@/components/folders/PendingFolderInvites";
import { NarrowBackHeader } from "@/components/layout/NarrowBackHeader";
import { SearchField } from "@/components/ui";
import { WorkspaceTrashSection } from "@/components/workspace/TrashSection";
import { useConversations } from "@/hooks/useConversations";
import { getFolders, useFolders } from "@/hooks/useFolders";
import { hasLocalFiles } from "@/lib/capabilities";
import { sortFoldersByRecentActivity } from "@/lib/draftWorkspaceFolders";
import type { FileSource } from "@/lib/fileSource";
import {
  ancestorFolderIds,
  buildFolderTree,
  pruneFolderTree,
} from "@/lib/folderTree";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { useReadOnlyOffline } from "@/lib/offlineMode";
import { cn } from "@/lib/utils";
import {
  canWriteFolder,
  dedupeFoldersByLocalBinding,
  isSharedWithMeFolder,
} from "@/services/folders";
import { createDocumentSource } from "@/services/sources/documentSource";
import {
  MEMORY_UPDATES_PATH,
  createMemorySource,
  parseProjectMemoryFolderId,
  parseProjectProfilePath,
} from "@/services/sources/memorySource";
import { asReadOnlyFileSource } from "@/services/sources/readOnlyFileSource";
import {
  createCloudWorkspaceSource,
  resolveWorkspaceSource,
} from "@/services/sources/workspaceSource";
import type { WorkspaceInfo } from "@/services/workspaces";
import { useFoldersStore } from "@/stores/folders";
import { FileText, FolderOpen, Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/** Synthetic workspace id every memory leaf's tab lives under — they belong to no real
 * workspace (private per-user data), so they're resolved to {@link createMemorySource}
 * directly (path-aware: one source serves all leaves) and exempted from the "workspace
 * gone → close its tabs" cleanup. */
const MEMORY_WS = "__memory__";

/** Synthetic workspace id every rule-doc tab lives under — user rules are private per-user
 * documents belonging to no real workspace, resolved to {@link createDocumentSource} directly
 * (path-aware: the tab path IS the doc id) and exempted from the "workspace gone → close its
 * tabs" cleanup, 照 {@link MEMORY_WS}. */
const RULES_WS = "__rules__";

/**
 * The 文件 hub's **split** file UI (VSCode 式左树右详情). The left rail is
 * zones (双模式工作区 §5.4 / §八 — 界面上没有「项目」，容器只有文件夹):
 *
 * - **我的文件** — the cloud folder tree the user owns, nested by `rel_path`.
 * - **本机文件夹** — disk folders, most recently active first (VS Code 语义).
 * - **与我共享** — cloud desks this user joined (flat roots; same `folder:` tree).
 *
 * `conv:` scratch is conversation-panel addressing, not a hub zone: 裸聊写盘
 * 自动建桌，产物进「我的文件」。
 *
 * 段**默认折叠**（只露根标题），点标题展开/收起、展开态持久化（`expandedWs`）；折叠时不
 * 挂载 {@link FileTree}，故云端 eager 源的「整树递归拉取」推迟到展开时才发——文件夹一多时
 * 既清爽又省掉打开页面即 N 次全量请求。顶层另挂全局 ``AgentCore/``（条目 + 最近更新），
 * 不再三分「记忆 / 规则 / 文档」夹。The right pane is a **tab strip** — opening files
 * stacks tabs, each {@link FileDetail} stays mounted (hidden when inactive) so
 * switching never drops editor / draft state. The tree always stays visible (unlike
 * the swap-style {@link FileBrowser} used in narrow side panels).
 *
 * Rows come from the folder list rather than `/v1/workspaces`, so a folder just
 * created shows up before the workspace list refetches; a folder with no
 * workspace row yet falls back to {@link folderWorkspaceFallback}. Lifecycle
 * (new file·folder / upload / reveal in OS / open chat / delete folder / rename)
 * lives on each root's **right-click menu** to keep the rail clean; the rail header is a **name + path
 * filter** (real-time, case-insensitive substring over folder names and, for
 * expanded trees, file/folder names + relative paths; session-only, not persisted
 * — it's a search, not a preference). No content full-text search.
 *
 * The two container actions §5.4 leaves are the zone headers' own: 我的文件「+」
 * builds a cloud folder (nested via a row's「在此新建文件夹」), 本机文件夹「+」opens
 * one off the disk. Chats live on `/conversations`; the two cross-link — a root's
 * 「查看对话」jumps here→there, and「浏览文件」jumps there→here (via `focusWsId`
 * = `folder:<id>`，which expands + highlights the target root).
 */
export function FileWorkbench({
  workspaces,
  isLoading,
  isError,
  onRetry,
  fsAvailable,
  showMemory,
  focusWsId,
  focusKey,
  openMemoryLeaf,
}: {
  workspaces: WorkspaceInfo[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  fsAvailable: boolean;
  /** Show the pinned global entries rail atop the file hub.
   * 侧栏把文件夹条目挂进 ``.agentcore`` 树行；本 flag 只管 FileWorkbench 宿主。 */
  showMemory?: boolean;
  /** When navigated here with a target workspace (`/conversations`「浏览文件」),
   * auto-expand + highlight + scroll to that section（段默认折叠，故主动展开那一个；
   * 嵌套文件夹连同祖先一起展开）。`focusKey` (= navigation key) makes re-focusing the
   * same folder on a later jump fire again. */
  focusWsId?: string | null;
  /** When navigated here from a对话页「记忆已更新」card deep-link, open this exact memory
   * leaf as a tab (记忆更新对话内可见 §1.6). Gated on `focusKey` so it fires once per
   * navigation. Optional `projectId` falls back when `path` does not encode a folder id. */
  openMemoryLeaf?: {
    path: string;
    name: string;
    projectId?: string | null;
  } | null;
  focusKey?: string;
}) {
  const { isNarrow } = useNarrowLayoutState();
  const offline = useReadOnlyOffline();
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [railWidth, setRailWidth] = useState<number>(() => loadRailWidth());
  // 默认折叠的工作区段里，被展开过的那些（持久化）。详见 WS_EXPANDED_KEY 注释。
  const [expandedWs, setExpandedWs] = useState<Set<string>>(() =>
    loadExpandedWs(),
  );
  // 按名称/路径实时过滤（会话级瞬态，不持久化——它是搜索而非偏好）。
  const [filter, setFilter] = useState("");
  // 树内兄弟排序（名称 / 大小 / 修改时间）。与筛选相反，这是偏好，跨会话保留。
  const [sortBy, setSortBy] = useState<FileSortBy>(() => loadFileSort());
  // 从 /conversations「浏览文件」跳来时高亮的工作区根（1.5s 后消失，呼应对话页的 flash）。
  const [flashWsId, setFlashWsId] = useState<string | null>(null);
  // 最近更新 / 对话卡深链到文件夹条目时，强制展开该文件夹下的 AgentCore（一次性）。
  const [revealMemoryFolderId, setRevealMemoryFolderId] = useState<
    string | null
  >(null);
  // 深链到全局条目时，强制展开顶层 AgentCore（一次性）。
  const [revealGlobalAgentCore, setRevealGlobalAgentCore] = useState(false);
  const appliedFocusRef = useRef<string | null>(null);
  const appliedMemoryLeafRef = useRef<string | null>(null);

  const conversations = useConversations();
  const folders = useFolders();
  const openCreateFolder = useFoldersStore((s) => s.openCreateFolder);

  /** Folder workspaces only — `conv:` scratch is conversation-panel addressing. */
  const personalWorkspaces = useMemo(
    () => workspaces.filter((w) => w.wsId.startsWith("folder:")),
    [workspaces],
  );

  /** Every rail row's workspace, folder rows included even when `/v1/workspaces`
   * has not caught up with a folder the user just created. */
  const railWorkspaces = useMemo(() => {
    const known = new Set(personalWorkspaces.map((w) => w.wsId));
    const extra = folders
      .filter((f) => !known.has(`folder:${f.id}`))
      .map(folderWorkspaceFallback);
    return [...personalWorkspaces, ...extra];
  }, [personalWorkspaces, folders]);

  const railWorkspaceByWsId = useMemo(
    () => new Map(railWorkspaces.map((w) => [w.wsId, w])),
    [railWorkspaces],
  );

  const toggleWs = useCallback((wsId: string) => {
    setExpandedWs((prev) => {
      const next = new Set(prev);
      if (next.has(wsId)) next.delete(wsId);
      else next.add(wsId);
      saveExpandedWs(next);
      return next;
    });
  }, []);

  const expandWs = useCallback((...wsIds: string[]) => {
    setExpandedWs((prev) => {
      if (wsIds.every((id) => prev.has(id))) return prev;
      const next = new Set(prev);
      for (const id of wsIds) next.add(id);
      saveExpandedWs(next);
      return next;
    });
  }, []);

  const clearMemoryReveal = useCallback(() => {
    setRevealMemoryFolderId(null);
    setRevealGlobalAgentCore(false);
  }, []);

  /** Expand project AgentCore for a deep-linked memory leaf. */
  const revealMemoryInRail = useCallback(
    (path: string, projectId?: string | null) => {
      const folderId = parseProjectMemoryFolderId(path) ?? projectId ?? null;
      setFilter("");
      if (folderId) {
        const wsId = `folder:${folderId}`;
        expandWs(wsId);
        setRevealMemoryFolderId(folderId);
        setFlashWsId(wsId);
        window.setTimeout(() => setFlashWsId(null), 1500);
      } else {
        setRevealGlobalAgentCore(true);
      }
    },
    [expandWs],
  );

  // 拖拽分隔条调左栏宽度：拖动期用窗口级监听 + 锁 body 光标/选区（避免拖过右侧编辑器时选中文本），
  // 松手落盘最终宽度（持久化，下次进页面沿用）。
  const startRailDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = railWidth;
    let latest = startW;
    const onMove = (ev: PointerEvent) => {
      latest = clampRail(startW + (ev.clientX - startX));
      setRailWidth(latest);
    };
    const onUp = () => {
      saveRailWidth(latest);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const nudgeRail = (delta: number) => {
    setRailWidth((w) => {
      const next = clampRail(w + delta);
      saveRailWidth(next);
      return next;
    });
  };

  // 工作区被删/消失 → 关掉它名下的标签页，并修正激活项。记忆 tab（合成 ws）不属任何工作区，
  // 故豁免，否则它会被立刻清掉。协作桌与自有夹都是 `folder:` ws_id。
  useEffect(() => {
    const liveWsIds = new Set(railWorkspaces.map((w) => w.wsId));
    const live = tabs.filter(
      (t) =>
        t.wsId === MEMORY_WS || t.wsId === RULES_WS || liveWsIds.has(t.wsId),
    );
    if (live.length === tabs.length) return;
    setTabs(live);
    if (activeKey && !live.some((t) => tabKey(t.wsId, t.path) === activeKey)) {
      setActiveKey(live.length ? tabKey(live[0].wsId, live[0].path) : null);
    }
  }, [railWorkspaces, tabs, activeKey]);

  // 从 /conversations「浏览文件」跳来：自动展开 + 高亮 + 滚入目标工作区（段默认折叠，故这里
  // 主动展开那一个）。每个 focusKey（导航键）只应用一次，但等到工作区列表就绪后才生效（冷进入
  // /files 时列表可能尚未加载）。
  useEffect(() => {
    if (!focusWsId || !focusKey) return;
    if (appliedFocusRef.current === focusKey) return;
    const known = railWorkspaces.some((w) => w.wsId === focusWsId);
    if (!known) return;
    appliedFocusRef.current = focusKey;
    setFilter(""); // 清掉过滤，避免目标工作区被筛掉而看不到
    // 嵌套文件夹要连同各层祖先一起展开，否则目标行仍藏在折叠的父级里。
    const folderId = folderIdOf(focusWsId);
    const ancestors = folderId
      ? ancestorFolderIds(folders, folderId).map((id) => `folder:${id}`)
      : [];
    expandWs(...ancestors, focusWsId);
    setFlashWsId(focusWsId);
    const t = setTimeout(() => setFlashWsId(null), 1500);
    return () => clearTimeout(t);
  }, [focusWsId, focusKey, railWorkspaces, folders, expandWs]);

  // 对话页「记忆已更新」卡片深链跳来：打开目标记忆叶子的 tab（记忆更新对话内可见 §1.6）。每个
  // focusKey（导航键）只应用一次。记忆源与工作区列表无关，故无需等 workspaces 就绪即可打开；
  // 文件夹画像叶子的双栏编辑器会在列表到位后自行解析文件夹名。内联开 tab 逻辑（与 openFile
  // 文件夹叶子额外展开对应文件夹 + ``.agentcore`` 节点；主题叶再展「主题」。
  useEffect(() => {
    if (!openMemoryLeaf || !focusKey) return;
    if (appliedMemoryLeafRef.current === focusKey) return;
    appliedMemoryLeafRef.current = focusKey;
    const { path, name, projectId } = openMemoryLeaf;
    const key = tabKey(MEMORY_WS, path);
    setTabs((prev) =>
      prev.some((t) => tabKey(t.wsId, t.path) === key)
        ? prev
        : [...prev, { wsId: MEMORY_WS, path, name }],
    );
    setActiveKey(key);
    revealMemoryInRail(path, projectId);
  }, [openMemoryLeaf, focusKey, revealMemoryInRail]);

  // 每个工作区一个稳定的 FileSource（树与详情共用，按 ws 复用，避免重复构建/反复重载）。
  // N4-A：离线时本地源只读包装；云源仍解析但 UI 灰显（不隐藏）。
  const sourceByWs = useMemo(() => {
    const m = new Map<string, FileSource | null>();
    const folderById = new Map(folders.map((f) => [f.id, f]));
    for (const w of railWorkspaces) {
      const folderId = folderIdOf(w.wsId);
      const folder = folderId ? folderById.get(folderId) : undefined;
      if (offline && w.location === "cloud") {
        m.set(w.wsId, null);
        continue;
      }
      if (folder?.mode === "cloud" && !canWriteFolder(folder)) {
        m.set(
          w.wsId,
          createCloudWorkspaceSource(w.wsId, w.name, { readonly: true }),
        );
        continue;
      }
      const src = resolveWorkspaceSource(w, fsAvailable);
      if (offline && src && w.location === "local") {
        m.set(w.wsId, asReadOnlyFileSource(src));
      } else {
        m.set(w.wsId, src);
      }
    }
    return m;
  }, [railWorkspaces, folders, fsAvailable, offline]);

  // 记忆叶子的路径感知单一源（所有记忆叶子共用一例，按 tab path 解析作用域；与工作区源同构，
  // 故复用 FileDetail/编辑器）。
  const memorySource = useMemo(() => createMemorySource(), []);

  // 规则叶子的路径感知单一源（tab path 即文档 id；与记忆源同构，故复用 FileDetail/编辑器）。
  const documentSource = useMemo(() => createDocumentSource(), []);

  // 名字匹配保留；已展开段也保留（否则按文件名筛时段落被名过滤藏掉，树内过滤无法露出）。
  // 树内路径/文件名过滤由各段 FileTree 的 filterQuery 完成。
  const matchesFilter = useCallback(
    (name: string, wsId: string) => {
      const q = filter.trim().toLowerCase();
      if (!q) return true;
      return name.toLowerCase().includes(q) || expandedWs.has(wsId);
    },
    [filter, expandedWs],
  );

  /** 我的文件 = 云端文件夹的真嵌套树；命中项的祖先一并保留，否则深处的匹配会没了路径。 */
  const cloudFolderNodes = useMemo(() => {
    const tree = buildFolderTree(
      folders.filter((f) => f.mode === "cloud" && !isSharedWithMeFolder(f)),
    );
    if (!filter.trim()) return tree;
    return pruneFolderTree(tree, (f) =>
      matchesFilter(f.name, `folder:${f.id}`),
    );
  }, [folders, filter, matchesFilter]);

  /** 本机文件夹 = 最近打开列表（VS Code 语义）：同一本机路径可能被多条文件夹记录绑定，去重后按最近活跃排。 */
  const localFolders = useMemo(() => {
    const local = dedupeFoldersByLocalBinding(
      folders.filter((f) => f.mode === "local"),
    );
    return sortFoldersByRecentActivity(local, conversations).filter((f) =>
      matchesFilter(f.name, `folder:${f.id}`),
    );
  }, [folders, conversations, matchesFilter]);

  const sharedWithMeFolders = useMemo(() => {
    return sortFoldersByRecentActivity(
      folders.filter(isSharedWithMeFolder),
      conversations,
    ).filter((f) => matchesFilter(f.name, `folder:${f.id}`));
  }, [folders, conversations, matchesFilter]);

  const treeFilterQuery = filter.trim();

  const railEmpty = folders.length === 0 && personalWorkspaces.length === 0;

  const activeTab = useMemo(
    () => tabs.find((t) => tabKey(t.wsId, t.path) === activeKey) ?? null,
    [tabs, activeKey],
  );

  // 打开文件：已开则激活其标签，未开则新增并激活（标签持久，直到手动关闭）。
  const openFile = (wsId: string, path: string, name: string) => {
    const key = tabKey(wsId, path);
    setTabs((prev) =>
      prev.some((t) => tabKey(t.wsId, t.path) === key)
        ? prev
        : [...prev, { wsId, path, name }],
    );
    setActiveKey(key);
  };

  /** Open a memory leaf and, for project-scoped paths, expand that project AgentCore. */
  const openMemoryLeafInRail = (
    path: string,
    name: string,
    projectId?: string | null,
  ) => {
    openFile(MEMORY_WS, path, name);
    revealMemoryInRail(path, projectId);
  };

  // 关标签：关的是激活页则跳到相邻页（优先右、否则左），全关则回空态。
  const closeTab = (key: string) => {
    const idx = tabs.findIndex((t) => tabKey(t.wsId, t.path) === key);
    if (idx === -1) return;
    const next = tabs.filter((_, i) => i !== idx);
    setTabs(next);
    if (activeKey === key) {
      const ni = Math.min(idx, next.length - 1);
      setActiveKey(ni >= 0 ? tabKey(next[ni].wsId, next[ni].path) : null);
    }
  };

  const openEntry = (target: EntryOpenTarget) => {
    if (target.channel === "memory") {
      openFile(MEMORY_WS, target.path, target.name);
    } else {
      openFile(RULES_WS, target.path, target.name);
    }
  };

  const closeEntry = (target: EntryOpenTarget) => {
    const wsId = target.channel === "memory" ? MEMORY_WS : RULES_WS;
    closeTab(tabKey(wsId, target.path));
  };

  const renameEntryTab = (target: EntryOpenTarget, name: string) => {
    const wsId = target.channel === "memory" ? MEMORY_WS : RULES_WS;
    setTabs((prev) =>
      prev.map((t) =>
        t.wsId === wsId && t.path === target.path ? { ...t, name } : t,
      ),
    );
  };

  // 只留这一页（其余全关），并将其设为激活。
  const closeOthers = (key: string) => {
    const keep = tabs.find((t) => tabKey(t.wsId, t.path) === key);
    if (!keep) return;
    setTabs([keep]);
    setActiveKey(key);
  };

  const closeAll = () => {
    setTabs([]);
    setActiveKey(null);
  };

  /** 本机文件夹段只在能读本机盘的宿主里出现（Web 版没有）。 */
  const localFsAvailable = fsAvailable && hasLocalFiles();

  /** One bundle every folder row reads from, instead of drilling a dozen props. */
  const railHost: FolderRailHost = {
    workspaceByWsId: railWorkspaceByWsId,
    sourceByWs,
    expandedWs,
    onToggleWs: toggleWs,
    onOpenFile: openFile,
    activeTab,
    flashWsId,
    filterQuery: treeFilterQuery,
    sortBy,
    offline,
    onCreateSubfolder: (parent, anchorEl) =>
      openCreateFolder(anchorEl ?? null, { id: parent.id, name: parent.name }),
    renderWorkroomLead: showMemory
      ? (folder, indent) => (
          <EntriesSection
            scope={{ kind: "folder", folderId: folder.id }}
            memoryActivePath={
              activeTab?.wsId === MEMORY_WS ? activeTab.path : null
            }
            documentActivePath={
              activeTab?.wsId === RULES_WS ? activeTab.path : null
            }
            onOpen={openEntry}
            onDeleted={closeEntry}
            onRenamed={renameEntryTab}
            indent={indent}
          />
        )
      : undefined,
    onCreateWorkroomEntry: showMemory
      ? (folder) =>
          createAndOpenScopeEntry(
            { kind: "folder", folderId: folder.id },
            openEntry,
          )
      : undefined,
    revealWorkroomFolderId: revealMemoryFolderId,
    onWorkroomRevealApplied: clearMemoryReveal,
  };

  const narrowDetail = isNarrow && tabs.length > 0;

  return (
    <div className="flex h-full w-full">
      {/* Left: workspaces + their files as one multi-root tree (resizable).
          窄屏无详情时占满；打开文件后推进到右栏（本树藏起）。 */}
      <aside
        style={isNarrow ? undefined : { width: railWidth }}
        className={cn(
          "flex shrink-0 flex-col border-r border-border",
          isNarrow && "min-w-0 flex-1 border-r-0",
          narrowDetail && "hidden",
        )}
      >
        {/* Rail header: name + in-tree path filter（新建走各段标题的「+」；
            段级 CRUD 在各 WorkspaceSection 右键菜单). */}
        <div className="flex h-12 shrink-0 items-center gap-1 border-b border-border px-2">
          <SearchField
            value={filter}
            onValueChange={setFilter}
            placeholder="筛选文件夹或文件…"
            aria-label="按名称筛选文件夹或文件"
            className="min-w-0 flex-1"
          />
          <FileSortMenu
            value={sortBy}
            onChange={(by) => {
              setSortBy(by);
              saveFileSort(by);
            }}
          />
        </div>

        {/* Pinned global entries + 最近更新. Per-folder entries mount inside
            each folder's ``.agentcore`` tree row. */}
        {showMemory && (
          <div className="shrink-0 border-b border-border px-2 py-1">
            <AgentCoreSection
              scope={{ kind: "global" }}
              memoryActivePath={
                activeTab?.wsId === MEMORY_WS ? activeTab.path : null
              }
              documentActivePath={
                activeTab?.wsId === RULES_WS ? activeTab.path : null
              }
              onOpenEntry={openEntry}
              onEntryDeleted={closeEntry}
              onEntryRenamed={renameEntryTab}
              onOpenUpdates={() =>
                openFile(MEMORY_WS, MEMORY_UPDATES_PATH, "记忆动态")
              }
              forceOpen={revealGlobalAgentCore}
              onRevealApplied={clearMemoryReveal}
            />
          </div>
        )}

        <PendingFolderInvites
          onAccepted={(folder) => {
            const wsId = `folder:${folder.id}`;
            expandWs(wsId);
            setFlashWsId(wsId);
            window.setTimeout(() => setFlashWsId(null), 1500);
          }}
        />

        {isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <Loader2
              size={18}
              className="animate-spin text-muted-foreground/50"
            />
          </div>
        ) : isError ? (
          <InlineError onRetry={onRetry} />
        ) : railEmpty ? (
          <EmptyHint
            icon={<FolderOpen size={24} className="text-muted-foreground/40" />}
            title="还没有文件夹"
            hint="在「我的文件」里新建文件夹或打开本机文件夹；别人邀请你的协作桌会出现在「与我共享」。"
          />
        ) : (
          <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 py-1">
            {filter.trim() &&
            cloudFolderNodes.length === 0 &&
            localFolders.length === 0 &&
            sharedWithMeFolders.length === 0 ? (
              <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                没有匹配「{filter.trim()}」的文件夹或已展开树中的文件
              </p>
            ) : (
              <>
                {(cloudFolderNodes.length > 0 || !filter.trim()) && (
                  <MyFilesRailHeader />
                )}
                {cloudFolderNodes.length === 0 && !filter.trim() ? (
                  <p className="px-2 py-2 text-xs text-muted-foreground/70">
                    还没有文件夹，用右上角「+」建一个
                  </p>
                ) : (
                  <FolderRailNodes nodes={cloudFolderNodes} host={railHost} />
                )}

                {localFsAvailable &&
                  (localFolders.length > 0 || !filter.trim()) && (
                    <LocalFoldersRailHeader />
                  )}
                {localFsAvailable &&
                  (localFolders.length === 0 && !filter.trim() ? (
                    <p className="px-2 py-2 text-xs text-muted-foreground/70">
                      打开过的本机文件夹会出现在这里
                    </p>
                  ) : (
                    localFolders.map((folder) => (
                      <FolderRailRow
                        key={folder.id}
                        folder={folder}
                        host={railHost}
                      />
                    ))
                  ))}

                {sharedWithMeFolders.length > 0 && (
                  <>
                    <SharedWithMeRailHeader />
                    {sharedWithMeFolders.map((folder) => (
                      <FolderRailRow
                        key={folder.id}
                        folder={folder}
                        host={railHost}
                      />
                    ))}
                  </>
                )}
              </>
            )}
          </div>
        )}
      </aside>

      {/* Draggable sash between tree and detail (keyboard: ←/→ to nudge). */}
      {!isNarrow && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="调整文件树宽度"
          tabIndex={0}
          onPointerDown={startRailDrag}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") {
              e.preventDefault();
              nudgeRail(-16);
            } else if (e.key === "ArrowRight") {
              e.preventDefault();
              nudgeRail(16);
            }
          }}
          style={{ touchAction: "none" }}
          className="z-10 w-1.5 shrink-0 cursor-col-resize transition-colors hover:bg-primary/20 focus-visible:bg-primary/30 focus-visible:outline-none"
        />
      )}

      {/* Right: open files as tabs; every open file stays mounted (hidden when
          inactive) so switching never unmounts an editor or drops unsaved /
          transient state. 窄屏无打开文件时不占一列空态。 */}
      {!(isNarrow && tabs.length === 0) && (
        <section className="flex min-w-0 flex-1 flex-col">
          {tabs.length === 0 ? (
            <EmptyHint
              inline
              icon={<FileText size={26} className="text-muted-foreground/40" />}
              title="选择一个文件"
              hint="从左侧的文件夹树里点开文件，可同时打开多个、用标签页来回切换。"
            />
          ) : (
            <>
              {isNarrow && (
                <NarrowBackHeader
                  title={activeTab?.name ?? "文件"}
                  onBack={() => {
                    if (activeKey) closeTab(activeKey);
                  }}
                />
              )}
              <DetailTabs
                tabs={tabs}
                activeKey={activeKey}
                onActivate={setActiveKey}
                onClose={closeTab}
                onCloseOthers={closeOthers}
                onCloseAll={closeAll}
              />
              <div className="relative min-h-0 flex-1">
                {tabs.map((t) => {
                  const key = tabKey(t.wsId, t.path);
                  // The synthetic「记忆动态」tab is not a file — render the cross-conversation
                  // feed view instead of a source-backed editor.
                  const isMemoryUpdates =
                    t.wsId === MEMORY_WS && t.path === MEMORY_UPDATES_PATH;
                  // 版本 / 软删区面板：挂在真实工作区下的合成 tab，不是文件，故不解析文件源。
                  const wsPanel =
                    t.wsId === MEMORY_WS ||
                    t.wsId === RULES_WS ||
                    (t.path !== WS_VERSIONS_PATH && t.path !== WS_TRASH_PATH)
                      ? null
                      : t.path;
                  const panelWsName =
                    railWorkspaceByWsId.get(t.wsId)?.name ?? t.name;
                  const src =
                    t.wsId === MEMORY_WS
                      ? memorySource
                      : t.wsId === RULES_WS
                        ? documentSource
                        : (sourceByWs.get(t.wsId) ?? null);
                  // A folder's 画像 leaf opens the two-pane 全局+本文件夹 editor instead of
                  // a lone file; resolve its live folder name for the 归属 label (fall back
                  // to stripping the tab name if the folder is gone).
                  const projFolderId =
                    t.wsId === MEMORY_WS
                      ? parseProjectProfilePath(t.path)
                      : null;
                  const projName = projFolderId
                    ? (getFolders().find((f) => f.id === projFolderId)?.name ??
                      t.name.replace(/·画像\.md$/, ""))
                    : null;
                  return (
                    <div
                      key={key}
                      className={cn(
                        "absolute inset-0",
                        key === activeKey ? "" : "hidden",
                      )}
                    >
                      {isMemoryUpdates ? (
                        <MemoryUpdatesView
                          onOpenLeaf={(path, name) =>
                            openMemoryLeafInRail(path, name)
                          }
                        />
                      ) : wsPanel === WS_VERSIONS_PATH ? (
                        <WorkspaceVersionsPanel
                          wsId={t.wsId}
                          name={panelWsName}
                        />
                      ) : wsPanel === WS_TRASH_PATH ? (
                        <WorkspaceTrashSection wsId={t.wsId} />
                      ) : src ? (
                        projFolderId ? (
                          <MemoryProfileSplitEditor
                            source={src}
                            folderId={projFolderId}
                            folderName={
                              projName ?? t.name.replace(/·画像\.md$/, "")
                            }
                            onClose={() => closeTab(key)}
                          />
                        ) : (
                          <FileDetail
                            source={src}
                            path={t.path}
                            name={t.name}
                            onClose={() => closeTab(key)}
                          />
                        )
                      ) : (
                        <EmptyHint
                          inline
                          icon={
                            <FileText
                              size={26}
                              className="text-muted-foreground/40"
                            />
                          }
                          title="无法打开此文件"
                          hint="它所属文件夹的文件源暂不可用。"
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}
