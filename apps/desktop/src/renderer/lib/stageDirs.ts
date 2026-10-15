/** 盘上约定根目录名——与后端 ``stage_dirs.AGENTCORE_ROOT`` 对齐。 */
export const AGENTCORE_ROOT = "GAgent";

/** Legacy on-disk AI-memory dir under the convention root. Hidden in the file tree like trash. */
export const AGENTCORE_MEMORY_DIR = "记忆";

/** True when `path` is `AgentCore/记忆` or a descendant (workspace-relative). */
export function isAgentCoreMemoryDirPath(path: string): boolean {
  const p = path.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!p || p === ".") return false;
  const prefix = `${AGENTCORE_ROOT}/${AGENTCORE_MEMORY_DIR}`;
  return p === prefix || p.startsWith(`${prefix}/`);
}

/** 是否工作区根下那个 ``AgentCore/``（嵌套的同名目录不算，它不是约定根）。 */
export function isAgentCoreRootDir(path: string): boolean {
  return normalizePath(path) === AGENTCORE_ROOT;
}

/** 规范化：去尾斜杠，POSIX 相对路径。 */
function normalizePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "");
}

export type ChildrenLookup = (
  dir: string,
) => { isDir: boolean; path: string }[] | undefined;

/** 统计目录下已加载的后代文件数（不含子目录本身）。未加载则按 0。 */
export function countDescendantFiles(
  dirPath: string,
  childrenOf: ChildrenLookup,
): number {
  const kids = childrenOf(dirPath);
  if (!kids) return 0;
  let n = 0;
  for (const c of kids) {
    if (c.isDir) n += countDescendantFiles(c.path, childrenOf);
    else n += 1;
  }
  return n;
}
