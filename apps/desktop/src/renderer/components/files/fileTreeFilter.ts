import type { FileNode } from "@/lib/fileSource";
import { parentDir } from "@/lib/fileSource";
import { AGENTCORE_ROOT_LABEL, isAgentCoreRootDir } from "@/lib/stageDirs";

/**
 * Client-side path/name filter for {@link FileTree} (no content search, no API).
 * Case-insensitive substring over `name` and relative `path` (Chinese paths OK —
 * `toLowerCase` is a no-op on CJK).
 */
export function matchesFileTreeQuery(
  node: Pick<FileNode, "name" | "path">,
  query: string,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    node.name.toLowerCase().includes(q) ||
    node.path.toLowerCase().includes(q) ||
    (isAgentCoreRootDir(node.path) &&
      AGENTCORE_ROOT_LABEL.toLowerCase().includes(q))
  );
}

export interface FileTreeFilterResult {
  /** Paths that should render (matches + ancestors of matches). */
  visible: Set<string>;
  /** Ancestor dirs of matches — overlay-expand while filtering (not persisted). */
  forceExpand: Set<string>;
}

/**
 * From already-loaded tree buckets, keep matching files/folders and their
 * ancestors; force-expand ancestors so matches stay reachable. Empty query →
 * empty sets (caller treats as "no filter").
 */
export function computeFileTreeFilter(
  childrenOf: (dir: string) => FileNode[] | undefined,
  query: string,
): FileTreeFilterResult {
  const visible = new Set<string>();
  const forceExpand = new Set<string>();
  const q = query.trim();
  if (!q) return { visible, forceExpand };

  const stack: string[] = [""];
  const seen = new Set<string>([""]);
  while (stack.length > 0) {
    const dir = stack.pop();
    if (dir === undefined) break;
    const kids = childrenOf(dir);
    if (!kids) continue;
    for (const node of kids) {
      if (matchesFileTreeQuery(node, q)) {
        visible.add(node.path);
        let cur = parentDir(node.path);
        while (cur !== "") {
          visible.add(cur);
          forceExpand.add(cur);
          cur = parentDir(cur);
        }
      }
      if (node.isDir && !seen.has(node.path)) {
        seen.add(node.path);
        stack.push(node.path);
      }
    }
  }
  return { visible, forceExpand };
}
