import { Button, IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  type FileNode,
  type FileSource,
  baseName,
  joinPath,
  parentDir,
} from "@/lib/fileSource";
import { notifyActionError, notifyError } from "@/lib/toast";
import {
  ChevronsDownUp,
  FilePlus,
  FileText,
  FolderPlus,
  Loader2,
  RefreshCw,
} from "lucide-react";
import type React from "react";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { FileTreeBatchDialogs } from "./FileTreeBatchDialogs";
import { InlineCreateRow } from "./FileTreeInline";
import { FileTreeRow } from "./FileTreeRow";
import { FileTreeSelectionBar } from "./FileTreeSelectionBar";
import { UploadMenu } from "./UploadMenu";
import { dedupeName } from "./dedupeName";
import { setFileClipboard, useFileClipboard } from "./fileClipboard";
import {
  type BatchFailure,
  deleteRestoreHint,
  runBatch,
  withSkipped,
} from "./fileTreeBatch";
import { subscribeFileTreeChanged } from "./fileTreeBus";
import { loadExpanded, saveExpanded } from "./fileTreeExpanded";
import { computeFileTreeFilter } from "./fileTreeFilter";
import { flattenVisibleRows, topLevelSelection } from "./fileTreeSelection";
import type {
  FileSortBy,
  FileTreeChromeState,
  FileTreeHandle,
} from "./fileTreeTypes";
import { watchDirsForExpanded, withVirtualAgentCore } from "./fileTreeWorkroom";
import { Centered, EmptyHint, InlineError, TruncatedNotice } from "./parts";
import { useFileTreeBatch } from "./useFileTreeBatch";
import { useFileTreeData } from "./useFileTreeData";
import { useFileTreeDrop } from "./useFileTreeDrop";

export { dedupeName } from "./dedupeName";
export type { FileTreeChromeState, FileTreeHandle } from "./fileTreeTypes";

/** AI / watch / focus 连写合并窗口。人手点刷新不走这里。 */
export const FILE_TREE_SILENT_DEBOUNCE_MS = 200;

/**
 * The unified file tree for any {@link FileSource} (文件中枢统一 Step 0) — the one
 * tree that backs both the Files page (a local OS root) and the conversation
 * workspace panel (the server workspace). Capabilities gate the chrome: upload
 * appears only when the source can transfer bytes; live updates only when it can
 * watch. Interaction model is converged on inline create/rename + a right-click
 * context menu + drag-to-move (within the source), with per-source persisted
 * fold state. The container owns where a clicked file opens (via `onOpenFile`).
 */
interface FileTreeProps {
  source: FileSource;
  onOpenFile: (path: string, name: string) => void;
  activePath?: string | null;
  headerExtra?: React.ReactNode;
  /** 隐藏自带工具栏 + 自身高度/滚动（嵌入式多根堆叠用，由外层统一滚动）。 */
  chrome?: boolean;
  /**
   * 仅隐藏自带工具栏、但保留自身高度/滚动（chrome 模式下由外层接管工具栏、
   * 把文件操作经 {@link FileTreeHandle} ref 驱动；侧栏单行面板头用）。
   */
  hideToolbar?: boolean;
  /**
   * 工具栏相关状态变更回调（外置工具栏据此响应式渲染上传/折叠/刷新态）。
   * 调用方应传**稳定引用**（如 useState 的 setter），否则会按渲染抖动。
   */
  onChromeState?: (state: FileTreeChromeState) => void;
  /** 每行额外左内边距，用于把整棵树嵌套在某个标题（工作区根）之下。 */
  indent?: number;
  /** 嵌入模式（chrome=false）下根为空时的提示文案（默认「空文件夹」）。 */
  emptyText?: string;
  /**
   * 可选：按文件/文件夹名与相对路径做客户端即时过滤（大小写不敏感子串；
   * 匹配项可见并自动展开祖先；空串不过滤）。不下探文件内容、不发搜索 API。
   */
  filterQuery?: string;
  /**
   * 根层要藏起来的目录名——「我的文件」把子文件夹渲染成自己的 rail 行，
   * 若树里再列一遍，同一个文件夹会出现两次（且树里那份没有归属/记忆入口）。
   */
  hideRootDirs?: readonly string[];
  /**
   * 兄弟排序依据（默认按名称）。只重排已在内存里的层，不会重新拉取。
   */
  sortBy?: FileSortBy;
  /**
   * Bound-folder entries, rendered inside the expanded ``.agentcore`` row.
   * When set and the disk dir is missing, a virtual ``AgentCore`` row is prepended
   * so the entries still have a drawer.
   */
  renderWorkroomLead?: (indent: number) => React.ReactNode;
  /** Deep-link: expand these dirs once (``AgentCore`` for memory cards). */
  forceExpandPaths?: readonly string[];
  onForceExpandApplied?: () => void;
}

export const FileTree = forwardRef<FileTreeHandle, FileTreeProps>(
  function FileTree(
    {
      source,
      onOpenFile,
      activePath = null,
      headerExtra,
      chrome = true,
      hideToolbar = false,
      onChromeState,
      indent = 0,
      emptyText = "空文件夹",
      filterQuery = "",
      hideRootDirs,
      sortBy = "name",
      renderWorkroomLead,
      forceExpandPaths,
      onForceExpandApplied,
    },
    ref,
  ) {
    const data = useFileTreeData(source, sortBy);
    const [expanded, setExpanded] = useState<Set<string>>(() =>
      loadExpanded(source.id),
    );
    // Render / filter / keyboard-select-all share this view (virtual ``.agentcore``
    // + hideRootDirs). data.childrenOf stays the disk map for truncated counts.
    const childrenOf = withVirtualAgentCore(data.childrenOf, {
      injectVirtual: Boolean(renderWorkroomLead),
      hideRootDirs,
      sortBy,
    });
    const filterActive = filterQuery.trim().length > 0;
    // childrenOf reads live refs; this component re-renders on data bumps, so
    // recompute each render (no stale memo over unloaded dirs).
    const filterResult = filterActive
      ? computeFileTreeFilter(childrenOf, filterQuery)
      : null;
    const filterVisible = filterResult?.visible ?? null;
    const forceExpandKey = filterResult
      ? [...filterResult.forceExpand].sort().join("\0")
      : "";
    const effectiveExpanded = useMemo(() => {
      if (!forceExpandKey) return expanded;
      const next = new Set(expanded);
      for (const dir of forceExpandKey.split("\0")) {
        if (dir) next.add(dir);
      }
      return next;
    }, [expanded, forceExpandKey]);
    const [creating, setCreating] = useState<{
      dir: string;
      kind: "file" | "dir";
    } | null>(null);
    const [renaming, setRenaming] = useState<string | null>(null);
    const [dropTarget, setDropTarget] = useState<string | null>(null);
    // 剪贴板是**全局**一份：中枢把每个云文件夹渲染成独立的树，「在 A 剪、到 B 粘」
    // 是基本诉求，跟着树走就永远跨不过去（见 fileClipboard.ts）。
    const clipboard = useFileClipboard();

    // 选区（复制/剪切/删除的作用对象）与批量动作。可见行顺序即渲染顺序，Shift 连选据此取区间。
    const visibleRows = flattenVisibleRows({
      childrenOf,
      expanded: effectiveExpanded,
      filterVisible,
    });
    const batch = useFileTreeBatch({
      source,
      data,
      visibleRows,
      onCut: (paths) =>
        setFileClipboard({ op: "cut", sourceId: source.id, paths }),
    });
    const {
      clear: clearSelection,
      deselect,
      report: reportBatch,
      reloadDirs,
    } = batch;
    // 别的树剪走的东西不该在这棵树里画成半透明——路径可能刚好同名。
    const cutPaths = useMemo(
      () =>
        new Set(
          clipboard?.op === "cut" && clipboard.sourceId === source.id
            ? clipboard.paths
            : [],
        ),
      [clipboard, source.id],
    );

    // 换源只重置这棵树自己的东西——剪贴板是全局的，清掉就再也粘不到别的树里。
    useEffect(() => {
      setExpanded(loadExpanded(source.id));
      clearSelection();
    }, [source.id, clearSelection]);

    const sourceRef = useRef(source);
    sourceRef.current = source;
    const dataRef = useRef(data);
    dataRef.current = data;
    const expandedRef = useRef(effectiveExpanded);
    expandedRef.current = effectiveExpanded;
    const silentTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [watchEpoch, setWatchEpoch] = useState(0);

    const runSilentRefresh = useCallback(async () => {
      const tree = dataRef.current;
      const src = sourceRef.current;
      const dirs = src.listTree
        ? [""]
        : watchDirsForExpanded(expandedRef.current);
      const wasEmpty = dirs.filter(
        (dir) => (tree.childrenOf(dir)?.length ?? 0) === 0,
      );
      await Promise.all(dirs.map((dir) => tree.reloadSilent(dir)));
      // 本机空→有：第一笔把目录写出来之后，原先挂在不存在路径上的 watch 是空的，
      // 必须重挂；不换 source（换对象会冲掉树）。
      if (
        src.caps.watch &&
        wasEmpty.some((dir) => (tree.childrenOf(dir)?.length ?? 0) > 0)
      ) {
        setWatchEpoch((n) => n + 1);
      }
    }, []);

    const scheduleSilentRefresh = useCallback(() => {
      if (silentTimerRef.current != null) {
        clearTimeout(silentTimerRef.current);
      }
      silentTimerRef.current = setTimeout(() => {
        silentTimerRef.current = null;
        void runSilentRefresh();
      }, FILE_TREE_SILENT_DEBOUNCE_MS);
    }, [runSilentRefresh]);

    useEffect(
      () => () => {
        if (silentTimerRef.current != null) {
          clearTimeout(silentTimerRef.current);
        }
      },
      [],
    );

    // Live updates: watch the root + every expanded dir (local FS only).
    // AI / watch 走 silent，禁止 data.reload（那条会打 chrome.loading）。
    // biome-ignore lint/correctness/useExhaustiveDependencies: source.id / caps.watch / watchEpoch re-subscribe; body reads sourceRef
    useEffect(() => {
      const watch = sourceRef.current.watch;
      if (!watch || !sourceRef.current.caps.watch) return;
      const offs = watchDirsForExpanded(effectiveExpanded).map((dir) =>
        watch(dir, () => scheduleSilentRefresh()),
      );
      return () => {
        for (const off of offs) off();
      };
    }, [
      source.id,
      source.caps.watch,
      effectiveExpanded,
      scheduleSilentRefresh,
      watchEpoch,
    ]);

    // Cloud / no-watch sources never push mutations — silent re-pull on focus
    // (不轮询、不给云源 watch:true)。人手刷新仍走下面的 refresh()。
    useEffect(() => {
      if (source.caps.watch) return;
      const onFocus = () => scheduleSilentRefresh();
      window.addEventListener("focus", onFocus);
      return () => window.removeEventListener("focus", onFocus);
    }, [source.caps.watch, scheduleSilentRefresh]);

    // fileTreeBus：AI 写盘与跨源搬运都通知「这棵树某层变了」。订阅侧走 silent。
    useEffect(
      () =>
        subscribeFileTreeChanged((change) => {
          if (change.sourceId === source.id) scheduleSilentRefresh();
        }),
      [source.id, scheduleSilentRefresh],
    );

    const toggle = useCallback(
      (dir: string) => {
        setExpanded((prev) => {
          const next = new Set(prev);
          if (next.has(dir)) next.delete(dir);
          else {
            next.add(dir);
            data.ensureDir(dir);
          }
          saveExpanded(source.id, next);
          return next;
        });
      },
      [data, source.id],
    );

    const workroomExpandKey = (forceExpandPaths ?? []).join("\0");
    const revealAppliedRef = useRef(false);
    useEffect(() => {
      if (!workroomExpandKey) {
        revealAppliedRef.current = false;
        return;
      }
      if (revealAppliedRef.current) return;
      revealAppliedRef.current = true;
      const paths = workroomExpandKey.split("\0").filter(Boolean);
      setExpanded((prev) => {
        const next = new Set(prev);
        for (const p of paths) {
          next.add(p);
          data.ensureDir(p);
        }
        saveExpanded(source.id, next);
        return next;
      });
      onForceExpandApplied?.();
    }, [workroomExpandKey, data, source.id, onForceExpandApplied]);

    const collapseAll = useCallback(() => {
      saveExpanded(source.id, new Set());
      setExpanded(new Set());
    }, [source.id]);

    const refresh = useCallback(() => {
      data.reload("");
      for (const dir of effectiveExpanded) data.reload(dir);
    }, [data, effectiveExpanded]);

    const openCreate = useCallback(
      (dir: string, kind: "file" | "dir") => {
        if (dir !== "") {
          setExpanded((prev) => {
            if (prev.has(dir)) return prev;
            const next = new Set(prev).add(dir);
            data.ensureDir(dir);
            saveExpanded(source.id, next);
            return next;
          });
        }
        setCreating({ dir, kind });
      },
      [data, source.id],
    );

    const submitCreate = useCallback(
      async (rawName: string) => {
        const target = creating;
        setCreating(null);
        if (!target) return;
        const name = rawName.trim().replace(/^\/+|\/+$/g, "");
        if (!name || name.includes("/")) return;
        const path = joinPath(target.dir, name);
        try {
          if (target.kind === "dir") await source.mkdir(path);
          else await source.createFile(path);
          data.reload(target.dir);
          if (target.kind === "file") onOpenFile(path, name);
        } catch {
          notifyError("已存在同名文件或文件夹，或创建失败");
        }
      },
      [creating, source, data, onOpenFile],
    );

    const submitRename = useCallback(
      async (path: string, rawName: string) => {
        setRenaming(null);
        const name = rawName.trim();
        if (!name || name === baseName(path)) return;
        if (name.includes("/")) {
          notifyError("名称不能包含「/」");
          return;
        }
        const dst = joinPath(parentDir(path), name);
        try {
          await source.move(path, dst);
          data.reload(parentDir(path));
        } catch {
          notifyError("已存在同名文件，或重命名失败");
        }
      },
      [source, data],
    );

    const remove = useCallback(
      async (node: FileNode) => {
        const what = node.isDir ? "文件夹及其全部内容" : "文件";
        // Soft-delete honesty: cloud → AgentCore/trash（软删区）；local → OS
        // recycle bin（`trashPath` / `shell.trashItem`，失败不硬删）。Hint 与批量删除同一句。
        const restoreHint = deleteRestoreHint(source);
        if (
          !window.confirm(`确定删除${what}「${node.name}」？${restoreHint}`)
        ) {
          return;
        }
        try {
          await source.delete(node.path);
          data.reload(parentDir(node.path));
          clearSelection();
        } catch {
          notifyError("删除失败");
        }
      },
      [source, data, clearSelection],
    );

    // 与右键菜单一致：无 caps.edit 时不进剪贴板/粘贴（只读源菜单也不挂这些项）。
    const canMutate = source.caps.edit;

    const doCopy = useCallback(
      (paths: string[]) => {
        if (!canMutate || !source.copy || paths.length === 0) return;
        setFileClipboard({ op: "copy", sourceId: source.id, paths });
      },
      [canMutate, source],
    );

    const doCut = useCallback(
      (paths: string[]) => {
        if (!canMutate || paths.length === 0) return;
        setFileClipboard({ op: "cut", sourceId: source.id, paths });
      },
      [canMutate, source],
    );

    // 把目标目录展开（若折叠），让粘贴结果立即可见；随后由调用方 reload。
    const revealDir = useCallback(
      (dir: string) => {
        if (dir === "") return;
        setExpanded((prev) => {
          if (prev.has(dir)) return prev;
          const next = new Set(prev).add(dir);
          data.ensureDir(dir);
          saveExpanded(source.id, next);
          return next;
        });
      },
      [data, source.id],
    );

    // 拖拽落点 / 上传入口 / 跨源搬运（见 useFileTreeDrop）——不自己管选中态与行渲染，
    // 只把「搬走了哪几项」回给这里摘选区，免得「往树里放东西」和「在树里选东西」挤成一段。
    const drop = useFileTreeDrop({
      source,
      data,
      canMutate,
      revealDir,
      onDropTarget: setDropTarget,
      reportBatch,
      reloadDirs,
      onMoved: deselect,
    });

    // 把剪贴板内容粘贴进 destDir（""=根）。剪切走必备的 move（全源可用，一次性）；复制走可选
    // copy（本地 IPC / 云端 REST），名字按目标目录现有项去重（副本 / 副本 2…），可重复粘贴。
    // 多项粘贴 = 逐项调单项端点，故一项撞名不连累其余项：逐项记账，末尾一次报清哪几项没成。
    const doPaste = useCallback(
      async (destDir: string) => {
        if (!canMutate) return;
        const clip = clipboard;
        if (!clip || clip.paths.length === 0) return;
        if (clip.op === "copy" && !source.copy) return;
        const verb = clip.op === "cut" ? "移动" : "复制";
        // 来自别的树：父子云文件夹在盘上本就是一棵树，借共同祖先工作区的 move/copy 搬。
        if (clip.sourceId !== source.id) {
          reportBatch(verb, await drop.pasteAcross(clip, destDir));
          return;
        }
        let names: Set<string>;
        try {
          const siblings = await source.listDir(destDir);
          names = new Set(siblings.map((n) => n.name));
        } catch (e) {
          notifyActionError("粘贴失败", e);
          return;
        }
        const skipped: BatchFailure[] = [];
        const pending: string[] = [];
        for (const path of clip.paths) {
          const name = baseName(path);
          if (destDir === path || destDir.startsWith(`${path}/`)) {
            skipped.push({ path, name, reason: "不能粘贴到自身或其子目录" });
            continue;
          }
          if (clip.op === "cut") {
            if (parentDir(path) === destDir) continue; // 原地剪切粘贴 = 空操作
            if (names.has(name)) {
              skipped.push({ path, name, reason: "目标位置已存在同名项" });
              continue;
            }
            names.add(name); // 同批里后一项不能再占这个名字
          }
          pending.push(path);
        }
        const outcome = await runBatch(pending, async (path) => {
          const name = baseName(path);
          if (clip.op === "cut") {
            await source.move(path, joinPath(destDir, name));
            return;
          }
          const copyName = dedupeName(name, names);
          names.add(copyName); // 连粘多项时对刚占下的名字继续去重
          await source.copy?.(path, joinPath(destDir, copyName));
        });
        // 剪切一次性——但只在**真搬走了**东西之后：整批撞名 / 全是原地粘贴时保住剪贴板，
        // 用户还能换个地方粘（与跨源路径 pasteAcross 同一口径）。
        if (clip.op === "cut" && outcome.done > 0) {
          setFileClipboard(null);
          // 选区里那几行已经搬走了，留着它等于让下一次删除对着不存在的路径开火。
          clearSelection();
        }
        // 复制保留剪贴板，可重复粘贴（每次对最新清单去重）。
        revealDir(destDir);
        reloadDirs([
          destDir,
          ...(clip.op === "cut" ? clip.paths.map(parentDir) : []),
        ]);
        const result = withSkipped(skipped, outcome);
        // 整批都是原地粘贴：什么也没发生，不必报账（剪贴板照旧留着，剪切还没落地）。
        if (result.done === 0 && result.failures.length === 0) return;
        reportBatch(verb, result);
      },
      [
        canMutate,
        clipboard,
        source,
        revealDir,
        reloadDirs,
        reportBatch,
        clearSelection,
        drop,
      ],
    );

    // Esc 清空选区；Delete/Backspace 删选中项（多选走批量确认）；Ctrl/Cmd + A 全选可见行、
    // C/X/V 剪贴板。仅当焦点在树内（行按钮）时触发；输入框 / 创建·重命名态让出；有文本选区时
    // 让出原生复制。与菜单一致：无 caps.edit 时快捷键也不做复制/剪切/粘贴/删除。
    const onTreeKeyDown = useCallback(
      (e: React.KeyboardEvent) => {
        if (creating || renaming) return;
        const tag = (e.target as HTMLElement).tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        const items = batch.selection.items;
        if (e.key === "Escape") {
          if (items.length > 0) {
            e.preventDefault();
            clearSelection();
          }
          return;
        }
        if (
          (e.key === "Delete" || e.key === "Backspace") &&
          !e.ctrlKey &&
          !e.metaKey &&
          !e.altKey
        ) {
          if (items.length === 0 || !canMutate) return;
          e.preventDefault();
          if (items.length > 1) {
            batch.requestDelete();
            return;
          }
          const only = items[0];
          void remove({
            path: only.path,
            name: baseName(only.path),
            isDir: only.isDir,
          });
          return;
        }
        if (!(e.ctrlKey || e.metaKey)) return;
        const key = e.key.toLowerCase();
        if (key === "a") {
          e.preventDefault();
          batch.selectAllVisible();
          return;
        }
        if (window.getSelection()?.toString()) return;
        if (!canMutate) return;
        // 祖先已选中的后代不进剪贴板：父目录一走，子项路径就不成立了。
        const paths = topLevelSelection(items).map((i) => i.path);
        if (key === "c") {
          if (paths.length > 0 && source.copy) {
            e.preventDefault();
            doCopy(paths);
          }
        } else if (key === "x") {
          if (paths.length > 0) {
            e.preventDefault();
            doCut(paths);
          }
        } else if (key === "v" && clipboard) {
          e.preventDefault();
          // 粘贴落点 = 锚点行（目录本身 / 文件的父目录），无选区则落根。
          const anchor =
            items.find((i) => i.path === batch.selection.anchor) ?? items[0];
          const destDir = anchor
            ? anchor.isDir
              ? anchor.path
              : parentDir(anchor.path)
            : "";
          void doPaste(destDir);
        }
      },
      [
        creating,
        renaming,
        batch,
        clearSelection,
        clipboard,
        canMutate,
        source,
        remove,
        doCopy,
        doCut,
        doPaste,
      ],
    );

    const rootStatus = data.statusOf("");
    const loadedRootChildren = data.childrenOf("");
    const rootChildren = childrenOf("");
    const visibleRootChildren = filterVisible
      ? (rootChildren ?? []).filter((n) => filterVisible.has(n.path))
      : (rootChildren ?? []);
    const filterEmpty =
      filterActive &&
      rootChildren !== undefined &&
      rootChildren.length > 0 &&
      visibleRootChildren.length === 0;

    useImperativeHandle(
      ref,
      () => ({
        startCreate: (kind) => openCreate("", kind),
        refresh,
        triggerUpload: drop.triggerUpload,
        triggerUploadFolder: drop.triggerUploadFolder,
        collapseAll,
      }),
      [
        openCreate,
        refresh,
        collapseAll,
        drop.triggerUpload,
        drop.triggerUploadFolder,
      ],
    );

    // Mirror toolbar-relevant state up so an external toolbar (e.g. the side
    // panel's single header row) can render upload/collapse/refresh reactively
    // while still driving the actions through the ref.
    useEffect(() => {
      onChromeState?.({
        uploading: drop.uploading,
        hasExpanded: expanded.size > 0,
        loading: rootStatus === "loading",
      });
    }, [onChromeState, drop.uploading, expanded, rootStatus]);

    const canUpload = source.caps.transfer && canMutate;

    // 加载 / 错误 / 空：有 chrome（独占面板）时居中铺满；嵌入堆叠时收成左对齐小行。
    const loadingEl = chrome ? (
      <Centered>
        <Loader2 size={18} className="animate-spin text-muted-foreground/50" />
      </Centered>
    ) : (
      <div
        className="flex items-center gap-1.5 py-2 text-xs text-muted-foreground"
        style={{ paddingLeft: indent + 8 }}
      >
        <Loader2 size={12} className="animate-spin" />
        加载中…
      </div>
    );

    const errorEl = chrome ? (
      <InlineError onRetry={() => data.reload("")} />
    ) : (
      <div
        className="flex items-center gap-2 py-2 text-xs text-muted-foreground"
        style={{ paddingLeft: indent + 8 }}
      >
        加载失败
        <Button
          variant="ghost"
          onClick={() => data.reload("")}
          className="h-auto px-0 py-0 underline-offset-2 hover:underline"
        >
          重试
        </Button>
      </div>
    );

    const emptyEl = chrome ? (
      <EmptyHint
        inline
        icon={<FileText size={22} className="text-muted-foreground/40" />}
        title="暂无文件"
        hint={
          canUpload
            ? "拖拽文件到此处，或点「上传」「新建」开始。"
            : canMutate
              ? "点「新建」开始，或在此文件夹放入文件。"
              : "此工作区为只读。"
        }
      />
    ) : (
      <div
        className="py-1 text-xs text-muted-foreground/60"
        style={{ paddingLeft: indent + 8 }}
      >
        {emptyText}
      </div>
    );

    // 多选（≥2 项）才把行菜单换成批量菜单；源既不能改、也没有可下载的文件时不挂（空菜单）。
    const canDownloadBatch = source.caps.transfer && !!source.download;
    const multiSelected = batch.count >= 2;
    const batchMenu =
      multiSelected &&
      (canMutate || (canDownloadBatch && batch.downloadableCount > 0))
        ? {
            count: batch.count,
            downloadableCount: batch.downloadableCount,
            onDownload: batch.runDownload,
            onCut: batch.runCut,
            onDelete: batch.requestDelete,
          }
        : null;

    const selectionBar = multiSelected ? (
      <FileTreeSelectionBar
        count={batch.count}
        downloadableCount={batch.downloadableCount}
        canDownload={canDownloadBatch}
        canMutate={canMutate}
        busy={batch.busy}
        indent={indent}
        onDownload={batch.runDownload}
        onCut={batch.runCut}
        onDelete={batch.requestDelete}
        onClear={batch.clear}
      />
    ) : null;

    const batchDialogs = (
      <FileTreeBatchDialogs
        confirm={batch.confirm}
        onConfirmDelete={batch.confirmDelete}
        onCancelDelete={batch.cancelDelete}
        failure={batch.failure}
        onCloseFailure={batch.closeFailure}
      />
    );

    const body =
      rootStatus === "error" ? (
        errorEl
      ) : rootChildren === undefined ? (
        loadingEl
      ) : rootChildren.length === 0 && !creating ? (
        emptyEl
      ) : filterEmpty && !creating ? (
        chrome ? (
          <EmptyHint
            inline
            icon={<FileText size={22} className="text-muted-foreground/40" />}
            title="无匹配文件"
            hint="试试其它关键词，或清空筛选。"
          />
        ) : (
          <div
            className="py-1 text-xs text-muted-foreground/60"
            style={{ paddingLeft: indent + 8 }}
          >
            无匹配文件
          </div>
        )
      ) : (
        <ul>
          {creating?.dir === "" && (
            <InlineCreateRow
              kind={creating.kind}
              depth={0}
              indentBase={indent}
              onSubmit={submitCreate}
              onCancel={() => setCreating(null)}
            />
          )}
          {visibleRootChildren.map((node) => (
            <FileTreeRow
              key={node.path}
              node={node}
              depth={0}
              indentBase={indent}
              source={source}
              data={data}
              expanded={effectiveExpanded}
              filterVisible={filterVisible}
              activePath={activePath}
              creating={creating}
              renaming={renaming}
              dropTarget={dropTarget}
              selectedPaths={batch.selectedPaths}
              dragPaths={batch.topLevelPaths}
              cutPaths={cutPaths}
              hasClipboard={clipboard !== null}
              batchMenu={batchMenu}
              onToggle={toggle}
              onOpenFile={onOpenFile}
              onSelect={batch.selectRowAt}
              onContextSelect={batch.selectForContextMenu}
              onContextCreate={openCreate}
              onStartRename={setRenaming}
              onSubmitRename={submitRename}
              onCancelRename={() => setRenaming(null)}
              onSubmitCreate={submitCreate}
              onCancelCreate={() => setCreating(null)}
              onDelete={remove}
              onCopy={doCopy}
              onCut={doCut}
              onPaste={doPaste}
              onMoveInto={drop.onMoveInto}
              onUpload={drop.onUpload}
              onDropTarget={setDropTarget}
              onReloadDir={(dir) => data.reload(dir)}
              renderWorkroomLead={renderWorkroomLead}
            />
          ))}
          {data.truncatedOf("") && (
            <TruncatedNotice
              indent={indent + 8}
              shown={loadedRootChildren?.length ?? 0}
            />
          )}
        </ul>
      );

    // 嵌入模式：无工具栏、无自身高度/滚动，撑内容高度；横向内边距由外层左栏统一给。
    if (!chrome) {
      return (
        <div onKeyDown={onTreeKeyDown} {...drop.rootDragProps}>
          {drop.chrome}
          {selectionBar}
          {body}
          {batchDialogs}
        </div>
      );
    }

    return (
      <div
        className="flex h-full flex-col"
        onKeyDown={onTreeKeyDown}
        {...drop.rootDragProps}
      >
        {drop.chrome}
        {!hideToolbar && (
          <div className="flex shrink-0 items-center gap-1 px-3 py-2">
            {canMutate || canUpload ? (
              <div className="flex items-center gap-2">
                {canMutate ? (
                  <div className="flex items-center gap-1">
                    <SimpleTooltip label="新建文件">
                      <IconButton
                        onClick={() => openCreate("", "file")}
                        aria-label="新建文件"
                      >
                        <FilePlus size={14} />
                      </IconButton>
                    </SimpleTooltip>
                    <SimpleTooltip label="新建文件夹">
                      <IconButton
                        onClick={() => openCreate("", "dir")}
                        aria-label="新建文件夹"
                      >
                        <FolderPlus size={14} />
                      </IconButton>
                    </SimpleTooltip>
                  </div>
                ) : null}
                {canUpload ? (
                  <UploadMenu
                    uploading={drop.uploading}
                    onUploadFiles={drop.triggerUpload}
                    onUploadFolder={drop.triggerUploadFolder}
                  />
                ) : null}
              </div>
            ) : null}
            <div className="flex-1" />
            {expanded.size > 0 && (
              <SimpleTooltip label="全部折叠">
                <IconButton onClick={collapseAll} aria-label="全部折叠">
                  <ChevronsDownUp size={14} />
                </IconButton>
              </SimpleTooltip>
            )}
            <SimpleTooltip label="刷新">
              <IconButton
                disabled={rootStatus === "loading"}
                onClick={refresh}
                aria-label="刷新"
              >
                {rootStatus === "loading" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <RefreshCw size={14} />
                )}
              </IconButton>
            </SimpleTooltip>
            {headerExtra}
          </div>
        )}

        {drop.dragOver && canUpload && (
          <div className="mx-3 mb-2 shrink-0 rounded-lg border border-dashed border-primary bg-primary/5 px-3 py-4 text-center text-xs text-primary">
            松开以上传到此处
          </div>
        )}

        {selectionBar && (
          <div className="mx-2 mb-1 shrink-0">{selectionBar}</div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">{body}</div>
        {batchDialogs}
      </div>
    );
  },
);
