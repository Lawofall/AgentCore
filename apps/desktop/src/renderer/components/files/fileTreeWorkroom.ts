import type { FileNode } from "@/lib/fileSource";
import {
  AGENTCORE_ROOT,
  DOCS_PREFIX,
  isAgentCoreRootDir,
} from "@/lib/stageDirs";
import type { FileSortBy } from "./fileTreeTypes";
import { sortNodes } from "./useFileTreeData";

/** Disk-shaped virtual row when entries exist but ``AgentCore/`` is not on disk. */
export const VIRTUAL_AGENTCORE: FileNode = {
  path: AGENTCORE_ROOT,
  name: AGENTCORE_ROOT,
  isDir: true,
};

/**
 * Same children source for render, filter, and keyboard/select-all.
 * Root view: hide rail-owned dirs, then prepend {@link VIRTUAL_AGENTCORE} when
 * entries exist but the disk listing has no ``AgentCore/``.
 */
export function withVirtualAgentCore(
  childrenOf: (dir: string) => FileNode[] | undefined,
  opts: {
    injectVirtual: boolean;
    hideRootDirs?: readonly string[];
    sortBy?: FileSortBy;
  },
): (dir: string) => FileNode[] | undefined {
  const { injectVirtual, hideRootDirs, sortBy = "name" } = opts;
  return (dir) => {
    const loaded = childrenOf(dir);
    if (dir !== "") return loaded;
    if (!loaded) return loaded;
    const hidden = hideRootDirs?.length
      ? loaded.filter((n) => !(n.isDir && hideRootDirs.includes(n.name)))
      : loaded;
    if (injectVirtual && !hidden.some((n) => isAgentCoreRootDir(n.path))) {
      return sortNodes([VIRTUAL_AGENTCORE, ...hidden], sortBy);
    }
    return hidden;
  };
}

/**
 * Local watch / silent refresh dirs: root + expanded, plus the flattened
 * ``AgentCore/文档`` seam when the ``.agentcore`` drawer is open.
 */
export function watchDirsForExpanded(expanded: Iterable<string>): string[] {
  const dirs = new Set<string>(["", ...expanded]);
  for (const dir of expanded) {
    if (isAgentCoreRootDir(dir)) dirs.add(DOCS_PREFIX);
  }
  return [...dirs];
}
