/**
 * 工作区路径救援 —— 与后端 ``normalize_workspace_path`` /
 * ``strip_root_label_prefix`` / ``_normalize_artifact_relpath`` 对齐
 * （``agentcore.workspace._paths``）。
 *
 * 模型常吐沙箱绝对路径（``/workspace/index.html``）或裸根 ``/`` / ``\\``；
 * 写工具落盘时会 normalize 成相对路径。桌面预览 / 终稿路径点击若原样只去前导 ``/``，
 * 会把根标签当成子目录（``workspace/index.html``）→ 上游 404。本模块是桌面侧
 * 同一语义的单一源；pathGuard 也走这里，禁止各 op 私有 ``if path==="/"``。
 */

/** 云端会话工作区默认根标签（与 ServerWorkspace.root_label 默认一致）。 */
export const DEFAULT_WORKSPACE_ROOT_LABEL = "workspace";

/**
 * 把 ``/<rootLabel>/…`` 绝对输入改写为工作区相对路径；相对输入原样返回。
 *
 * * ``/<rootLabel>/foo/bar.md`` → ``foo/bar.md``
 * * ``/<rootLabel>`` → ``.``
 * * ``workspace/foo``（无前导 ``/``）→ 原样（可能是真子目录）
 * * ``/etc/passwd`` → 原样（不同根，交给下游拒绝）
 */
export function stripRootLabelPrefix(
  relativePath: string,
  rootLabel: string = DEFAULT_WORKSPACE_ROOT_LABEL,
): string {
  if (!rootLabel) return relativePath;
  const normalized = relativePath.replace(/\\/g, "/");
  if (!normalized.startsWith("/")) return relativePath;
  const [first, ...restParts] = normalized.replace(/^\/+/, "").split("/");
  if (first !== rootLabel) return relativePath;
  const rest = restParts.join("/");
  return rest || ".";
}

/**
 * 工具路径契约：相对工作区根 POSIX；与后端 ``normalize_workspace_path`` 对齐。
 *
 * * 空 / ``.`` → ``.``
 * * 裸 ``/`` 或 ``\\`` → ``.``
 * * ``/<rootLabel>/…`` → strip
 * * 其它绝对路径原样（下游 pathGuard 拒绝）
 */
export function normalizeWorkspacePath(
  relativePath: string,
  rootLabel: string = DEFAULT_WORKSPACE_ROOT_LABEL,
): string {
  if (!relativePath || relativePath === ".") return ".";
  const unified = relativePath.replace(/\\/g, "/");
  if (unified === "/") return ".";
  return stripRootLabelPrefix(unified, rootLabel);
}

/**
 * 工具 / UI 入口路径 → 工作区相对 POSIX 路径（展示、去重、预览打开共用）。
 * 空 / 裸根 ``/workspace`` → ``""``（调用方应跳过）。
 */
export function toWorkspaceRelPath(
  path: string,
  rootLabel: string = DEFAULT_WORKSPACE_ROOT_LABEL,
): string {
  const raw = path.replace(/\\/g, "/").trim();
  if (!raw) return "";
  const stripped = normalizeWorkspacePath(raw, rootLabel);
  if (stripped === "." || stripped === "") return "";
  return stripped.replace(/^\.\/+/, "");
}
