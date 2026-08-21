import { type FileNode, type FileSource, parentDir } from "@/lib/fileSource";
import {
  DOCS_DIR_NAME,
  DOCS_PREFIX,
  flattenWorkroomListing,
  isAgentCoreRootDir,
} from "@/lib/stageDirs";
import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import type { FileSortBy } from "./fileTreeTypes";

export type DirStatus = "loading" | "ready" | "error";

/**
 * 兄弟排序档位：``.agentcore``（盘上 ``AgentCore/``）→ 目录 → 文件。抽屉默认折叠，
 * 钉在同级最前是为了侧栏窄视口里够得着，不跟整棵用户树抢滚动。
 */
function siblingRank(node: FileNode): number {
  if (isAgentCoreRootDir(node.path)) return 0;
  return node.isDir ? 1 : 2;
}

/** 降序比较一项可缺失的数值元信息（新的在前；缺失沉底）。 */
function compareDescNullable(
  a: number | null | undefined,
  b: number | null | undefined,
): number {
  const av = a ?? null;
  const bv = b ?? null;
  if (av === null && bv === null) return 0;
  if (av === null) return 1;
  if (bv === null) return -1;
  return bv - av;
}

/**
 * Dirs first, then by the chosen key — the canonical tree ordering (mutates +
 * returns). Name is always the tie-break, so equal timestamps still land
 * in a stable, readable order.
 */
export function sortNodes(
  nodes: FileNode[],
  by: FileSortBy = "name",
): FileNode[] {
  return nodes.sort((a, b) => {
    const rankA = siblingRank(a);
    const rankB = siblingRank(b);
    if (rankA !== rankB) return rankA - rankB;
    const keyed =
      by === "mtime" ? compareDescNullable(a.mtimeMs, b.mtimeMs) : 0;
    return keyed !== 0 ? keyed : a.name.localeCompare(b.name);
  });
}

/**
 * Fold a flat recursive listing into a per-parent children map (dir → its direct
 * children). The root bucket ("") is always present; every listed directory gets
 * an entry too (empty if it has no listed children) so the tree can render it as
 * a known-empty folder rather than a perpetual spinner.
 */
export function bucketTree(
  nodes: FileNode[],
  by: FileSortBy = "name",
): Map<string, FileNode[]> {
  const map = new Map<string, FileNode[]>([["", []]]);
  const bucket = (dir: string): FileNode[] => {
    let arr = map.get(dir);
    if (!arr) {
      arr = [];
      map.set(dir, arr);
    }
    return arr;
  };
  for (const n of nodes) {
    bucket(parentDir(n.path)).push(n);
    if (n.isDir) bucket(n.path);
  }
  for (const arr of map.values()) sortNodes(arr, by);
  return map;
}

export interface FileTreeData {
  /** Direct children of a directory, or undefined if not loaded yet. */
  childrenOf: (dir: string) => FileNode[] | undefined;
  statusOf: (dir: string) => DirStatus | undefined;
  /**
   * 这一层是否被后端条目上限截断（源不报告即为 false）。UI 必须把它显示出来——
   * 悄悄少几十个文件，用户读到的是「我的文件没了」。
   */
  truncatedOf: (dir: string) => boolean;
  /** Load a directory's children if not already loaded/loading (lazy sources). */
  ensureDir: (dir: string) => void;
  /** Reload one directory — eager sources reload the whole tree. Sets loading. */
  reload: (dir: string) => void;
  /**
   * 静默补丁：已 ready 的层不标 loading、不清空 children，只替换该层。
   * 从未加载的层跳过（不替用户展开）。急切源仍拉整树，但不打 loading。
   * AI / watch / focus 必须走这条，禁止走 {@link reload}（那条会打 chrome.loading）。
   */
  reloadSilent: (dir: string) => Promise<void>;
}

/**
 * The data layer behind {@link FileTree}, abstracting a source's two listing
 * styles behind one uniform read API (文件中枢统一 Step 0):
 *
 * - **eager** (`source.listTree` present, e.g. the server workspace): one
 *   recursive fetch buckets the whole tree into memory; every dir is `ready`,
 *   and reload re-fetches the lot. Natural for a small, server-enumerable space.
 * - **lazy** (`listDir` only, e.g. a local OS root): each directory loads on
 *   first expand; reload re-fetches just that level. Necessary for large trees.
 *
 * Either way the consumer just calls `childrenOf` / `ensureDir`; the rendering is
 * identical. Data lives in refs (mutated in place) with a version bump to
 * re-render, avoiding a fresh Map allocation per directory load.
 */
export function useFileTreeData(
  source: FileSource,
  sortBy: FileSortBy = "name",
): FileTreeData {
  const childrenRef = useRef<Map<string, FileNode[]>>(new Map());
  const statusRef = useRef<Map<string, DirStatus>>(new Map());
  const truncatedRef = useRef<Set<string>>(new Set());
  // Read by the loaders so changing the sort never invalidates them (that would
  // re-run the mount effect and refetch the whole tree just to reorder it).
  const sortRef = useRef(sortBy);
  // 重置只跟 source.id：同一工作区换了新对象（列表 refetch）不得清空树再转圈。
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const sourceId = source.id;
  const [, bump] = useReducer((n: number) => n + 1, 0);
  // 代次只跟 source.id：同树并行 listDir 共享一代，切源才作废。
  // 不能每请求 ++（那会让同层并行展开互相丢结果）。
  const genRef = useRef(0);
  const trackedSourceIdRef = useRef(sourceId);

  // 切源当帧清空，避免一帧「树还是 A、sourceRef 已是 B」时点开旧路径。
  if (trackedSourceIdRef.current !== sourceId) {
    trackedSourceIdRef.current = sourceId;
    genRef.current += 1;
    childrenRef.current = new Map();
    statusRef.current = new Map();
    truncatedRef.current = new Set();
  }

  const loadEager = useCallback(async (silent = false) => {
    const gen = genRef.current;
    const listTree = sourceRef.current.listTree;
    if (!listTree) return;
    const rootReady = statusRef.current.get("") === "ready";
    if (!silent) {
      statusRef.current.set("", "loading");
      bump();
    }
    try {
      const all = await listTree();
      if (gen !== genRef.current) return;
      childrenRef.current = bucketTree(all, sortRef.current);
      const status = new Map<string, DirStatus>();
      for (const dir of childrenRef.current.keys()) status.set(dir, "ready");
      statusRef.current = status;
    } catch {
      if (gen !== genRef.current) return;
      if (!silent || !rootReady) {
        statusRef.current = new Map([["", "error"]]);
      }
    }
    if (gen !== genRef.current) return;
    bump();
  }, []);

  const loadDir = useCallback(async (dir: string, silent = false) => {
    const gen = genRef.current;
    const src = sourceRef.current;
    const status = statusRef.current.get(dir);
    // 静默路径不替用户展开：没加载过的层跳过。loading / ready / error 才补丁。
    if (
      silent &&
      status !== "ready" &&
      status !== "error" &&
      status !== "loading"
    ) {
      return;
    }
    if (!silent) {
      statusRef.current.set(dir, "loading");
      bump();
    }
    try {
      // Prefer the bounded reader so a capped level can say so; sources that
      // enumerate in full only implement `listDir` and stay un-truncated.
      const bounded = src.listDirBounded;
      const res = bounded
        ? await bounded(dir)
        : { entries: await src.listDir(dir), truncated: false };
      if (gen !== genRef.current) return;
      childrenRef.current.set(dir, sortNodes(res.entries, sortRef.current));
      if (res.truncated) truncatedRef.current.add(dir);
      else truncatedRef.current.delete(dir);
      statusRef.current.set(dir, "ready");
    } catch {
      if (gen !== genRef.current) return;
      // 约定根 / 产物柜常常还不存在；当成空层，好让 ``.agentcore`` 仍能挂条目。
      const missingOk = isAgentCoreRootDir(dir) || dir === DOCS_PREFIX;
      if (missingOk) {
        childrenRef.current.set(dir, []);
        truncatedRef.current.delete(dir);
        statusRef.current.set(dir, "ready");
      } else if (!silent || status !== "ready") {
        statusRef.current.set(dir, "error");
      }
    }
    if (gen !== genRef.current) return;
    bump();
  }, []);

  // Reset + initial load only when the source *id* changes — not the object.
  // biome-ignore lint/correctness/useExhaustiveDependencies: sourceId is an intentional re-run key
  useEffect(() => {
    childrenRef.current = new Map();
    statusRef.current = new Map();
    truncatedRef.current = new Set();
    genRef.current += 1;
    bump();
    if (sourceRef.current.listTree) void loadEager();
    else void loadDir("");
  }, [sourceId, loadEager, loadDir]);

  // Switching the sort key reorders what's already in memory — never a refetch.
  useEffect(() => {
    sortRef.current = sortBy;
    for (const arr of childrenRef.current.values()) sortNodes(arr, sortBy);
    bump();
  }, [sortBy]);

  const ensureDir = useCallback(
    (dir: string) => {
      if (sourceRef.current.listTree) return; // whole tree already in memory
      if (isAgentCoreRootDir(dir)) {
        if (!statusRef.current.has(dir)) void loadDir(dir);
        if (!statusRef.current.has(DOCS_PREFIX)) void loadDir(DOCS_PREFIX);
        return;
      }
      if (statusRef.current.has(dir)) return; // loading / ready / error already
      void loadDir(dir);
    },
    [loadDir],
  );

  const reload = useCallback(
    (dir: string) => {
      if (sourceRef.current.listTree) void loadEager();
      else if (isAgentCoreRootDir(dir)) {
        void loadDir(dir);
        void loadDir(DOCS_PREFIX);
      } else void loadDir(dir);
    },
    [loadEager, loadDir],
  );

  const reloadSilent = useCallback(
    (dir: string) => {
      if (sourceRef.current.listTree) return loadEager(true);
      if (isAgentCoreRootDir(dir)) {
        return Promise.all([
          loadDir(dir, true),
          loadDir(DOCS_PREFIX, true),
        ]).then(() => undefined);
      }
      return loadDir(dir, true);
    },
    [loadEager, loadDir],
  );

  // Stable readers (read live refs) + a memoized facade so effects/handlers can
  // depend on `data` without re-firing every render; identity stays put across
  // source-object churn (same id). Re-renders are driven by `bump`.
  const childrenOf = useCallback((dir: string) => {
    if (isAgentCoreRootDir(dir)) {
      const own = childrenRef.current.get(dir);
      if (own === undefined) return undefined;
      const hasDocsDir = own.some((n) => n.isDir && n.name === DOCS_DIR_NAME);
      const docs = childrenRef.current.get(DOCS_PREFIX);
      if (hasDocsDir && docs === undefined) return undefined;
      return sortNodes(
        flattenWorkroomListing(own, docs ?? []),
        sortRef.current,
      );
    }
    return childrenRef.current.get(dir);
  }, []);
  const statusOf = useCallback((dir: string) => {
    if (isAgentCoreRootDir(dir)) {
      const ownStatus = statusRef.current.get(dir);
      const own = childrenRef.current.get(dir);
      if (own === undefined) return ownStatus;
      const hasDocsDir = own.some((n) => n.isDir && n.name === DOCS_DIR_NAME);
      if (hasDocsDir) {
        const docsStatus = statusRef.current.get(DOCS_PREFIX);
        const docs = childrenRef.current.get(DOCS_PREFIX);
        if (docs === undefined) {
          return docsStatus === "error" ? "error" : (docsStatus ?? "loading");
        }
      }
      return ownStatus;
    }
    return statusRef.current.get(dir);
  }, []);
  const truncatedOf = useCallback(
    (dir: string) => truncatedRef.current.has(dir),
    [],
  );

  return useMemo(
    () => ({
      childrenOf,
      statusOf,
      truncatedOf,
      ensureDir,
      reload,
      reloadSilent,
    }),
    [childrenOf, statusOf, truncatedOf, ensureDir, reload, reloadSilent],
  );
}
