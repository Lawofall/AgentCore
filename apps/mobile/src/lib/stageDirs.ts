/**
 * 约定文档约定目录（``AgentCore/文档/{工作稿,research,debate,reviews}/``）的中性元信息——
 * 文件浏览器徽章与产物卡标签共用。与后端 ``workspace.stage_dirs`` 对齐；无匹配则零噪音。
 * 约定根本身的呈现名（``.agentcore``）也在这里，同属「按路径给盘上目录配文案」。
 */

/** 盘上约定根目录名——与后端 ``stage_dirs.AGENTCORE_ROOT`` 对齐（磁盘真名，勿改）。 */
export const AGENTCORE_ROOT = "AgentCore";
/** 盘上产物柜名；UI 摊平进 ``.agentcore``，不单独露这一层。 */
export const DOCS_DIR_NAME = "文档";
export const DOCS_PREFIX = `${AGENTCORE_ROOT}/${DOCS_DIR_NAME}`;
/** 步 3 迁移归档；展开 ``.agentcore`` 时不露。 */
export const MIGRATED_MEMORY_DIR = "已迁入记忆";
export const DRAFTS_DIR = `${DOCS_PREFIX}/工作稿`;
export const RESEARCH_DIR = `${DOCS_PREFIX}/research`;
export const DEBATE_DIR = `${DOCS_PREFIX}/debate`;
export const REVIEWS_DIR = `${DOCS_PREFIX}/reviews`;

/**
 * 工作区根下 ``AgentCore/`` 的呈现名：过程稿合成一个抽屉。
 * 仅改显示：磁盘路径仍是 ``AgentCore/``，不迁盘、不改注入。
 * 手机 lite 不挂文件夹设定条目（无 folder scope）。
 */
export const AGENTCORE_ROOT_LABEL = ".agentcore";

export const AGENTCORE_ROOT_TOOLTIP = `这个文件夹里给 AI 用的过程稿（盘上 ${AGENTCORE_ROOT}/）；成品会归位到工作区`;

/** 是否工作区根下那个 ``AgentCore/``（嵌套的同名目录不算，它不是约定根）。 */
export function isAgentCoreRootDir(path: string): boolean {
  return normalizePath(path) === AGENTCORE_ROOT;
}

export interface StageDirMeta {
  key: string;
  label: string;
  tooltip: string;
}

const STAGE_DIRS: Record<string, StageDirMeta> = {
  [DRAFTS_DIR]: {
    key: "工作稿",
    label: "工作稿",
    tooltip: "AI 干活的过程材料默认落点；成品会归位到工作区",
  },
  [RESEARCH_DIR]: {
    key: "research",
    label: "调研约定文档",
    tooltip: "团队协作阶段产物，后续阶段会读取",
  },
  [DEBATE_DIR]: {
    key: "debate",
    label: "辩论产物",
    tooltip: "团队协作阶段产物，后续阶段会读取",
  },
  [REVIEWS_DIR]: {
    key: "reviews",
    label: "审查",
    tooltip: "审查与质检副产物",
  },
};

function normalizePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "");
}

export function stageDirMeta(path: string): StageDirMeta | null {
  const p = normalizePath(path);
  if (!p) return null;
  return STAGE_DIRS[p] ?? null;
}

export function stageFileLabel(path: string): string | null {
  const p = normalizePath(path);
  for (const [dir, meta] of Object.entries(STAGE_DIRS)) {
    if (p === dir || p.startsWith(`${dir}/`)) return meta.label;
  }
  return null;
}

/**
 * 把 ``AgentCore/文档/`` 的子项提升为 ``AgentCore/`` 的子项（去掉 ``文档/`` 空壳，
 * 并丢掉迁移归档）。调用方负责排序。
 */
export function flattenWorkroomListing<T extends { name: string }>(
  agentCoreChildren: readonly T[],
  docsChildren: readonly T[],
): T[] {
  const own = agentCoreChildren.filter((n) => n.name !== DOCS_DIR_NAME);
  const docs = docsChildren.filter((n) => n.name !== MIGRATED_MEMORY_DIR);
  return [...own, ...docs];
}

/** ``AgentCore/文档`` 对用户就是抽屉本身，浏览 cwd 收成约定根。 */
export function canonicalBrowseDir(dir: string): string {
  return dir === DOCS_PREFIX ? AGENTCORE_ROOT : dir;
}

export function displayDirName(path: string, name: string): string {
  return isAgentCoreRootDir(path) ? AGENTCORE_ROOT_LABEL : name;
}

export interface PresentCrumb {
  label: string;
  path: string;
}

/** 面包屑认呈现名，并跳过 ``文档/`` 空壳。 */
export function presentCrumbs(cwd: string): PresentCrumb[] {
  if (!cwd) return [];
  const segs = cwd.split("/").filter(Boolean);
  const out: PresentCrumb[] = [];
  let disk = "";
  for (const seg of segs) {
    disk = disk ? `${disk}/${seg}` : seg;
    if (disk === DOCS_PREFIX) continue;
    out.push({
      label: isAgentCoreRootDir(disk) ? AGENTCORE_ROOT_LABEL : seg,
      path: canonicalBrowseDir(disk),
    });
  }
  return out;
}

export function presentDirLabel(dir: string): string {
  if (!dir) return "根目录";
  const crumbs = presentCrumbs(dir);
  return crumbs[crumbs.length - 1]?.label ?? dir;
}

export function presentPathLabel(dir: string): string {
  if (!dir) return "根目录";
  const labels = presentCrumbs(dir).map((c) => c.label);
  return labels.length > 0 ? labels.join("/") : "根目录";
}

/** 当前目录搜索认呈现名（``.agentcore``）；不扫隐藏的 ``文档/`` 路径段。 */
export function matchesBrowseQuery(
  node: { name: string; path: string },
  query: string,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  if (node.name.toLowerCase().includes(q)) return true;
  return (
    isAgentCoreRootDir(node.path) &&
    AGENTCORE_ROOT_LABEL.toLowerCase().includes(q)
  );
}

function listingRank(node: { path: string; isDir: boolean }): number {
  if (isAgentCoreRootDir(node.path)) return 0;
  return node.isDir ? 1 : 2;
}

function sortListing<T extends { name: string; path: string; isDir: boolean }>(
  nodes: readonly T[],
): T[] {
  return [...nodes].sort((a, b) => {
    const ra = listingRank(a);
    const rb = listingRank(b);
    if (ra !== rb) return ra - rb;
    return a.name.localeCompare(b.name);
  });
}

/** 当前浏览层的用户面子项：约定根摊平四个稿夹，钉顶 ``.agentcore``。 */
export function workroomChildren<
  T extends { name: string; path: string; isDir: boolean },
>(tree: Map<string, T[]>, cwd: string): T[] {
  const dir = canonicalBrowseDir(cwd);
  const raw = isAgentCoreRootDir(dir)
    ? flattenWorkroomListing(tree.get(dir) ?? [], tree.get(DOCS_PREFIX) ?? [])
    : (tree.get(dir) ?? []);
  return sortListing(raw);
}

export type ChildrenLookup = (
  dir: string,
) => { isDir: boolean; path: string }[] | undefined;

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

export function stageDirCaption(meta: StageDirMeta, fileCount: number): string {
  return `${meta.label} · ${fileCount} 件`;
}
