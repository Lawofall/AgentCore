import type { FileNode, FileSource } from "@/lib/fileSource";
import { downloadSaveName, parentDir } from "@/lib/fileSource";
import { notifySuccess } from "@/lib/toast";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  BatchConfirmState,
  BatchFailureState,
} from "./FileTreeBatchDialogs";
import {
  type BatchOutcome,
  batchResultTitle,
  deleteRestoreHint,
  runBatch,
} from "./fileTreeBatch";
import { subscribeFileTreeChanged } from "./fileTreeBus";
import {
  EMPTY_SELECTION,
  type RowClickIntent,
  type SelectedItem,
  type TreeSelection,
  dropFromSelection,
  selectRow,
  selectionForContextMenu,
  selectionPaths,
  topLevelSelection,
} from "./fileTreeSelection";
import type { FileTreeData } from "./useFileTreeData";

/**
 * 文件树的多选与批量动作。
 *
 * 后端没有批量端点，批量删除 / 下载 / 移动都是**客户端逐项调既有单项端点**：删除仍走既有软删
 * （云端落 `AgentCore/trash`，逐项一条记录，故回收站里能逐项还原），下载仍是逐项另存（文件
 * 原样、文件夹为该子树 zip），移动仍是「剪切 → 粘贴到目标文件夹」（不新造目标选择面）。因此
 * 这里的核心职责不是"更快"，而是把**逐项成败**如实带出来交给 {@link FileTreeBatchDialogs}。
 */
export interface FileTreeBatch {
  selection: TreeSelection;
  selectedPaths: ReadonlySet<string>;
  /**
   * 批量动作**实际作用**的项数 = 选区去掉「祖先已选中」的后代。操作条与菜单都报这个数，
   * 否则会出现「删除 3 项」按下去、结果说「已删除 2 项」。
   */
  count: number;
  /** 同一批的路径（拖拽载荷用：拖选区内的行就是搬这一批）。 */
  topLevelPaths: readonly string[];
  /** 选区里能下载的项数（文件另存；文件夹整夹 zip）。无 `download` 的源为 0。 */
  downloadableCount: number;
  /** 行点击（含修饰键意图）后更新选区。 */
  selectRowAt: (node: FileNode, intent: RowClickIntent) => void;
  /** 右键落点：点在选区内保持整批，点在选区外收敛成单选。 */
  selectForContextMenu: (node: FileNode) => void;
  /** 全选当前可见行（Ctrl/Cmd + A）。 */
  selectAllVisible: () => void;
  clear: () => void;
  /** 摘掉已经搬走的行（本树内移动完成后调；跨源移动由总线送到来源树自己调）。 */
  deselect: (paths: readonly string[]) => void;
  /** 打开删除确认（清单 = 选区去掉「祖先已选中」的后代）。 */
  requestDelete: () => void;
  runDownload: () => void;
  runCut: () => void;
  /** 供树内其它批量路径（多项粘贴）复用同一套结果口径。 */
  report: (verb: string, outcome: BatchOutcome) => void;
  /** 批量改动后刷新受影响目录（急切源一次全树，惰性源逐目录）。 */
  reloadDirs: (dirs: Iterable<string>) => void;
  busy: boolean;
  confirm: BatchConfirmState | null;
  failure: BatchFailureState | null;
  confirmDelete: () => void;
  cancelDelete: () => void;
  closeFailure: () => void;
}

export function useFileTreeBatch(opts: {
  source: FileSource;
  data: FileTreeData;
  /** 当前可见行（渲染顺序）——Shift 连选与全选都以它为准。 */
  visibleRows: readonly SelectedItem[];
  /** 把这批路径放进树内剪贴板（批量移动 = 剪切 + 粘贴到目标文件夹）。 */
  onCut: (paths: string[]) => void;
}): FileTreeBatch {
  const { source, data, visibleRows, onCut } = opts;
  const [selection, setSelection] = useState<TreeSelection>(EMPTY_SELECTION);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<BatchConfirmState | null>(null);
  const [failure, setFailure] = useState<BatchFailureState | null>(null);

  // 可见行每渲染一次就是个新数组，放进 useCallback 依赖会让所有回调每帧换身份；用 ref 取最新
  // 值即可（用户点击总在 effect 之后发生）。
  const visibleRef = useRef(visibleRows);
  useEffect(() => {
    visibleRef.current = visibleRows;
  });

  const selectedPaths = useMemo(() => selectionPaths(selection), [selection]);
  // 祖先已选中的后代不单独动手：父目录一走，子项路径就不成立了。删除 / 剪切 / 拖拽都
  // 对这一批生效，计数与清单也一律以它为准。
  const topLevel = useMemo(
    () => topLevelSelection(selection.items),
    [selection],
  );
  const topLevelPaths = useMemo(() => topLevel.map((i) => i.path), [topLevel]);

  const clear = useCallback(() => setSelection(EMPTY_SELECTION), []);

  const deselect = useCallback((paths: readonly string[]) => {
    setSelection((prev) => dropFromSelection(prev, paths));
  }, []);

  // 别的树把本树的东西搬走了（跨源移动）：那几行已经不在盘上，留在选区里等于让下一次
  // 删除对着不存在的路径开火——与同源粘贴后清选区同一个理由。
  useEffect(
    () =>
      subscribeFileTreeChanged((change) => {
        if (change.sourceId !== source.id) return;
        if (change.movedAway?.length) deselect(change.movedAway);
      }),
    [source.id, deselect],
  );

  const selectRowAt = useCallback((node: FileNode, intent: RowClickIntent) => {
    setSelection((prev) => selectRow(prev, node, intent, visibleRef.current));
  }, []);

  const selectForContextMenu = useCallback((node: FileNode) => {
    setSelection((prev) => selectionForContextMenu(prev, node));
  }, []);

  const selectAllVisible = useCallback(() => {
    const rows = visibleRef.current;
    if (rows.length === 0) return;
    setSelection({
      items: [...rows],
      anchor: rows[rows.length - 1]?.path ?? null,
    });
  }, []);

  const report = useCallback((verb: string, outcome: BatchOutcome) => {
    if (outcome.failures.length === 0) {
      if (outcome.done > 0) notifySuccess(batchResultTitle(verb, outcome));
      return;
    }
    setFailure({
      title: batchResultTitle(verb, outcome),
      failures: outcome.failures,
    });
  }, []);

  // 惰性源逐个目录刷；`listTree` 的急切源每次 reload 都是重拉全树，逐目录调等于拉 N 遍。
  const reloadDirs = useCallback(
    (dirs: Iterable<string>) => {
      if (source.listTree) {
        data.reload("");
        return;
      }
      for (const dir of new Set(dirs)) data.reload(dir);
    },
    [source, data],
  );

  const requestDelete = useCallback(() => {
    if (topLevel.length === 0) return;
    setConfirm({
      items: topLevel,
      restoreHint: deleteRestoreHint(source),
      busy: false,
    });
  }, [topLevel, source]);

  const cancelDelete = useCallback(() => {
    setConfirm((prev) => (prev?.busy ? prev : null));
  }, []);

  const confirmDelete = useCallback(() => {
    const target = confirm;
    if (!target || target.busy) return;
    void (async () => {
      setConfirm({ ...target, busy: true });
      setBusy(true);
      const outcome = await runBatch(
        target.items.map((i) => i.path),
        (path) => source.delete(path),
      );
      reloadDirs(target.items.map((i) => parentDir(i.path)));
      setBusy(false);
      setConfirm(null);
      clear();
      report("删除", outcome);
    })();
  }, [confirm, source, reloadDirs, clear, report]);

  const downloadableCount = source.download ? topLevel.length : 0;

  const runDownload = useCallback(() => {
    const download = source.download;
    if (!download) return;
    if (topLevel.length === 0) return;
    void (async () => {
      setBusy(true);
      const outcome = await runBatch(
        topLevel.map((i) => i.path),
        (path) => {
          const item = topLevel.find((i) => i.path === path);
          const isDir = item?.isDir ?? false;
          return download(path, downloadSaveName(path, isDir), { isDir });
        },
      );
      setBusy(false);
      report("下载", outcome);
    })();
  }, [source, topLevel, report]);

  const runCut = useCallback(() => {
    if (topLevelPaths.length === 0) return;
    onCut([...topLevelPaths]);
  }, [topLevelPaths, onCut]);

  return {
    selection,
    selectedPaths,
    count: topLevel.length,
    topLevelPaths,
    downloadableCount,
    selectRowAt,
    selectForContextMenu,
    selectAllVisible,
    clear,
    deselect,
    requestDelete,
    runDownload,
    runCut,
    report,
    reloadDirs,
    busy,
    confirm,
    failure,
    confirmDelete,
    cancelDelete,
    closeFailure: () => setFailure(null),
  };
}
