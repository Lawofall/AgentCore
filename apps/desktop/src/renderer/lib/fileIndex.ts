/**
 * @ 提及的条目索引：聚合多个 FileSource（本地根 + 当前会话的云端工作区）的扁平
 * 文件清单 + 派生目录，并提供轻量过滤排序（文件中枢统一 F4）。
 *
 * 索引一次性经各源的 `listFileIndex` 拉取（每源有忽略规则 + 数量上限），在内存中
 * 按子串/子序列匹配，避免每次按键都走 IPC/REST。来源变动较少，组件生命周期内复用
 * 即可（切会话时由调用方失效重建，因云端工作区随会话而变）。
 *
 * 目录条目由文件路径的各级父目录去重派生（源只返回文件），选中目录时用
 * buildDirListing 生成「文件清单」作为上下文（不读取文件正文）。
 */

import {
  type FileIndexListing,
  type FileSource,
  baseName,
} from "@/lib/fileSource";

// "file" / "dir" come from FileSource indexing below; "conversation" / "document"
// entries are not indexed here — they are produced on demand (recent chats /
// on-demand settings) and reuse IndexedEntry so they flow through the same @ menu.
export type EntryKind = "file" | "dir" | "conversation" | "document";

export interface IndexedEntry {
  /** 来源标识（FileSource.id）：本地根 `local:<rootId>`、云端工作区 `workspace:<wsId>`。 */
  sourceId: string;
  /** 来源标签（项目/根名）。 */
  sourceLabel: string;
  /** 相对来源根的路径，分隔符统一为 "/"。 */
  relPath: string;
  name: string;
  /** "sourceLabel/relPath"，用于展示与匹配。 */
  display: string;
  kind: EntryKind;
  /** 最近修改（epoch ms）；索引未给出时缺省。空态排序用。 */
  mtimeMs?: number | null;
  /** 本机最近一次从 @ 菜单选中（epoch ms）；由调用方盖戳。 */
  lastUsedAt?: number | null;
}

export interface FileIndex {
  /** 文件条目（用于引用单文件，并作为派生目录清单的数据源）。 */
  files: IndexedEntry[];
  /** 目录条目（由文件路径的各级父目录去重派生）。 */
  dirs: IndexedEntry[];
  /** 可索引来源数量：用于区分「无来源」与「有来源但无文件」。 */
  sourceCount: number;
  /** 任一源的扁平索引被上限截断。调用方必须看见，不能当「根里就这些文件」。 */
  truncated: boolean;
}

/** 空态只出 1–2 层；更深路径与点路径要搜才见。 */
export const EMPTY_STATE_MAX_DEPTH = 2;

/**
 * 单一真实形状：`{ files: [{ relPath, mtimeMs? }], truncated }`。
 * 丢掉空路径与非正 mtime，避免空态把 0 当成「刚改过」。
 */
export function normalizeFileIndexListing(raw: FileIndexListing): {
  items: Array<{ relPath: string; mtimeMs?: number }>;
  truncated: boolean;
} {
  const items: Array<{ relPath: string; mtimeMs?: number }> = [];
  for (const file of raw.files) {
    if (!file.relPath) continue;
    items.push({
      relPath: file.relPath,
      mtimeMs:
        typeof file.mtimeMs === "number" && file.mtimeMs > 0
          ? file.mtimeMs
          : undefined,
    });
  }
  return { items, truncated: raw.truncated === true };
}

/**
 * 聚合多个来源的文件与目录。每个源经其 `listFileIndex` 提供扁平文件清单（本地根经
 * IPC、云端工作区经 REST，二者已对齐忽略规则与上限）；无 `listFileIndex` 的源跳过，
 * 单源读取失败时跳过该源，不影响其余。
 */
export async function loadFileIndex(sources: FileSource[]): Promise<FileIndex> {
  const files: IndexedEntry[] = [];
  // sourceId -> 该源下出现过的目录相对路径集合
  const dirPaths = new Map<string, Set<string>>();
  const labels = new Map<string, string>();
  let truncated = false;

  for (const source of sources) {
    if (!source.listFileIndex) continue;
    labels.set(source.id, source.label);
    let raw: FileIndexListing;
    try {
      raw = await source.listFileIndex();
    } catch {
      continue;
    }
    const listing = normalizeFileIndexListing(raw);
    if (listing.truncated) truncated = true;
    let dirs = dirPaths.get(source.id);
    if (!dirs) {
      dirs = new Set<string>();
      dirPaths.set(source.id, dirs);
    }
    for (const item of listing.items) {
      const rel = item.relPath;
      files.push({
        sourceId: source.id,
        sourceLabel: source.label,
        relPath: rel,
        name: baseName(rel),
        display: `${source.label}/${rel}`,
        kind: "file",
        mtimeMs: item.mtimeMs,
      });
      // 由文件路径派生各级父目录（去掉文件名后逐级累加）。
      const segs = rel.split("/");
      segs.pop();
      let acc = "";
      for (const seg of segs) {
        acc = acc ? `${acc}/${seg}` : seg;
        dirs.add(acc);
      }
    }
  }

  const dirMtime = new Map<string, number>();
  for (const f of files) {
    if (f.mtimeMs == null || f.mtimeMs <= 0) continue;
    const segs = f.relPath.split("/");
    segs.pop();
    let acc = "";
    for (const seg of segs) {
      acc = acc ? `${acc}/${seg}` : seg;
      const k = `${f.sourceId}\0${acc}`;
      const prev = dirMtime.get(k) ?? 0;
      if (f.mtimeMs > prev) dirMtime.set(k, f.mtimeMs);
    }
  }

  const dirs: IndexedEntry[] = [];
  for (const [sourceId, set] of dirPaths) {
    const label = labels.get(sourceId) ?? sourceId;
    for (const rel of set) {
      const slash = rel.lastIndexOf("/");
      const mtimeMs = dirMtime.get(`${sourceId}\0${rel}`);
      dirs.push({
        sourceId,
        sourceLabel: label,
        relPath: rel,
        name: slash >= 0 ? rel.slice(slash + 1) : rel,
        display: `${label}/${rel}`,
        kind: "dir",
        mtimeMs,
      });
    }
  }

  return { files, dirs, sourceCount: sources.length, truncated };
}

/** 子序列匹配：query 的字符按序出现在 text 中（不要求连续）。 */
function isSubsequence(query: string, text: string): boolean {
  let i = 0;
  for (let j = 0; j < text.length && i < query.length; j++) {
    if (text[j] === query[i]) i++;
  }
  return i === query.length;
}

function score(entry: IndexedEntry, q: string): number {
  const name = entry.name.toLowerCase();
  const display = entry.display.toLowerCase();
  if (name === q) return 100;
  if (name.startsWith(q)) return 90;
  if (name.includes(q)) return 70;
  if (display.includes(q)) return 50;
  if (isSubsequence(q, display)) return 30;
  return 0;
}

export function pathDepth(relPath: string): number {
  return relPath.split("/").filter((s) => s.length > 0).length;
}

/** 任一层段以 `.` 开头（`.cursor`、`.gitignore`）即点路径。 */
export function isDotPath(relPath: string): boolean {
  return relPath.split("/").some((seg) => seg.startsWith(".") && seg !== ".");
}

/** 空态可见：浅层（1–2 层）且非点路径。 */
export function isEmptyStateEligible(entry: IndexedEntry): boolean {
  const depth = pathDepth(entry.relPath);
  return (
    depth >= 1 && depth <= EMPTY_STATE_MAX_DEPTH && !isDotPath(entry.relPath)
  );
}

function emptyStateRank(a: IndexedEntry, b: IndexedEntry): number {
  const used = (b.lastUsedAt ?? 0) - (a.lastUsedAt ?? 0);
  if (used !== 0) return used;
  const mt = (b.mtimeMs ?? 0) - (a.mtimeMs ?? 0);
  if (mt !== 0) return mt;
  const depth = pathDepth(a.relPath) - pathDepth(b.relPath);
  if (depth !== 0) return depth;
  return a.display.localeCompare(b.display, "zh");
}

/** 空 query：浅层非点路径按最近使用 / 最近修改；有 query：全量子序列打分（含深层与点路径）。 */
export function filterEntries(
  index: IndexedEntry[],
  query: string,
  limit = 50,
): IndexedEntry[] {
  const q = query.trim().toLowerCase();
  if (!q) {
    return index
      .filter(isEmptyStateEligible)
      .sort(emptyStateRank)
      .slice(0, limit);
  }

  const scored: Array<{ entry: IndexedEntry; s: number }> = [];
  for (const entry of index) {
    const s = score(entry, q);
    if (s > 0) scored.push({ entry, s });
  }
  scored.sort(
    (a, b) => b.s - a.s || a.entry.display.localeCompare(b.entry.display, "zh"),
  );
  return scored.slice(0, limit).map((x) => x.entry);
}

/** 与 {@link filterEntries} 同一可见池的总数（未截 limit），供截断提示。 */
export function mentionFilterTotal(
  index: IndexedEntry[],
  query: string,
): number {
  const q = query.trim().toLowerCase();
  if (!q) return index.filter(isEmptyStateEligible).length;
  let n = 0;
  for (const entry of index) {
    if (score(entry, q) > 0) n++;
  }
  return n;
}

// 小目录平铺完整清单；超过此阈值切换为「目录树骨架 + 计数」概览。
const FLAT_CAP = 200;
const TREE_MAX_LINES = 200; // 骨架最多行数（防超大仓库目录数失控）
const TREE_MAX_DEPTH = 4; // 相对该目录展开的最大深度，更深由计数概括

export interface DirListing {
  /** 供模型阅读的目录上下文文本（小目录=文件清单；大目录=目录树骨架）。 */
  text: string;
  /** 信息被概括/截断（大目录骨架省略了文件名即视为 true）。 */
  truncated: boolean;
  /** 目录内（递归）文件总数。 */
  fileCount: number;
}

interface DirNode {
  name: string;
  /** 递归文件数。 */
  total: number;
  /** 直接位于该目录（非子目录）的文件数。 */
  direct: number;
  children: Map<string, DirNode>;
}

/**
 * 为目录构建上下文文本，分两档：
 * - 文件数 ≤ FLAT_CAP：平铺完整文件清单（相对路径，便于精确引用）。
 * - 文件数 > FLAT_CAP：目录树骨架——只列子目录并标注各自递归文件数，省略
 *   具体文件名，避免把超大目录的上千条路径塞进上下文。
 *
 * 全程不读取任何文件正文。数据源是已索引的 files（各源 listFileIndex 的递归结果，
 * 本身已忽略 node_modules/.git 等并有总数上限），按 sourceId + 前缀筛选。
 */
export function buildDirListing(
  files: IndexedEntry[],
  dir: IndexedEntry,
): DirListing {
  const prefix = `${dir.relPath}/`;
  const rels: string[] = [];
  for (const f of files) {
    if (f.sourceId !== dir.sourceId) continue;
    if (f.relPath.startsWith(prefix)) rels.push(f.relPath.slice(prefix.length));
  }
  rels.sort((a, b) => a.localeCompare(b, "zh"));
  const fileCount = rels.length;

  // 小目录：平铺完整清单。
  if (fileCount <= FLAT_CAP) {
    const header = `${dir.display}/ (${fileCount} 个文件)`;
    const body = rels.map((r) => `  ${r}`).join("\n");
    return {
      text: body ? `${header}\n${body}` : header,
      truncated: false,
      fileCount,
    };
  }

  // 大目录：聚合为目录树（子目录 + 递归/直接文件计数）。
  const root: DirNode = {
    name: dir.name,
    total: 0,
    direct: 0,
    children: new Map(),
  };
  for (const rel of rels) {
    const segs = rel.split("/");
    segs.pop(); // 去掉文件名，只留目录段
    root.total++;
    let node = root;
    for (const seg of segs) {
      let child = node.children.get(seg);
      if (!child) {
        child = { name: seg, total: 0, direct: 0, children: new Map() };
        node.children.set(seg, child);
      }
      child.total++;
      node = child;
    }
    node.direct++;
  }

  const lines: string[] = [];
  let over = false;
  const walk = (node: DirNode, depth: number, indent: string): void => {
    const kids = [...node.children.values()].sort((a, b) =>
      a.name.localeCompare(b.name, "zh"),
    );
    for (const k of kids) {
      if (lines.length >= TREE_MAX_LINES) {
        over = true;
        return;
      }
      lines.push(`${indent}${k.name}/ (${k.total})`);
      if (depth + 1 < TREE_MAX_DEPTH && k.children.size > 0) {
        walk(k, depth + 1, `${indent}  `);
      }
    }
  };
  walk(root, 0, "  ");
  if (root.direct > 0) {
    lines.push(`  （另有 ${root.direct} 个文件直接位于该目录）`);
  }
  if (over) lines.push("  …（更多子目录已省略）");

  const header = `${dir.display}/ (${fileCount} 个文件，目录结构概览；已省略文件名，括号内为各目录递归文件数)`;
  return { text: `${header}\n${lines.join("\n")}`, truncated: true, fileCount };
}
