/**
 * 区内引用：把「授权根 + 容器相对路径」收成当前工作区相对路径。
 * 纯字符串，主进程 / renderer 共用；绝对路径不进本模块。
 */

export function posixRel(path: string): string {
  return path.replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/+$/, "");
}

/**
 * ``dest`` 是本回合工作区（授权根 + 可选 subpath）。
 * ``cited`` 是文件相对**同一授权根**的路径。
 * 文件不在 dest 树内 → ``null``（应复制进 ``attachments/``）。
 */
export function workspaceRelFromCite(
  dest: { rootId: string; subpath?: string },
  cited: { rootId: string; relPath: string },
): string | null {
  if (dest.rootId !== cited.rootId) return null;
  const sub = posixRel(dest.subpath ?? "");
  const rel = posixRel(cited.relPath);
  if (!rel || rel === "." || rel.split("/").includes("..")) return null;
  if (!sub) return rel;
  if (rel === sub) return null;
  const prefix = `${sub}/`;
  if (!rel.startsWith(prefix)) return null;
  const rest = rel.slice(prefix.length);
  return rest || null;
}
