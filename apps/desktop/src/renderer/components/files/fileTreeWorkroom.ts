import type { FileNode } from "@/lib/fileSource";
import type { FileSortBy } from "./fileTreeTypes";

/**
 * Same children source for render, filter, and keyboard/select-all.
 * Root view hides rail-owned dirs. Disk listing is otherwise unchanged.
 */
export function withVirtualAgentCore(
  childrenOf: (dir: string) => FileNode[] | undefined,
  opts: {
    hideRootDirs?: readonly string[];
    sortBy?: FileSortBy;
  },
): (dir: string) => FileNode[] | undefined {
  const { hideRootDirs } = opts;
  return (dir) => {
    const loaded = childrenOf(dir);
    if (dir !== "") return loaded;
    if (!loaded) return loaded;
    if (!hideRootDirs?.length) return loaded;
    return loaded.filter((n) => !(n.isDir && hideRootDirs.includes(n.name)));
  };
}

/**
 * Local watch / silent refresh dirs: root + expanded.
 */
export function watchDirsForExpanded(expanded: Iterable<string>): string[] {
  return [...new Set(["", ...expanded])];
}
