/**
 * 约定文档约定目录（``AgentCore/文档/{工作稿,research,debate,reviews}/``）的中性元信息——
 * 文件树徽章与产物卡标签共用。与后端 ``workspace.stage_dirs`` 对齐；无匹配则零噪音。
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
 * 工作区根下 ``AgentCore/`` 的呈现名：文件夹设定条目 + 过程稿合成一个抽屉。
 * 仅改显示：磁盘路径仍是 ``AgentCore/``，不迁盘、不改注入。
 */
export const AGENTCORE_ROOT_LABEL = ".agentcore";

export const AGENTCORE_ROOT_TOOLTIP = `这个文件夹里给 AI 用的条目和过程稿（盘上 ${AGENTCORE_ROOT}/）；成品会归位到工作区`;

/** 是否工作区根下那个 ``AgentCore/``（嵌套的同名目录不算，它不是约定根）。 */
export function isAgentCoreRootDir(path: string): boolean {
  return normalizePath(path) === AGENTCORE_ROOT;
}

export interface StageDirMeta {
  /** 目录短名（工作稿 / research / debate / reviews） */
  key: string;
  /** 徽章主文案前缀，如「调研约定文档」 */
  label: string;
  /** tooltip */
  tooltip: string;
}

/** 约定目录完整相对路径 → 元信息（精确匹配）。 */
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

/** 规范化：去尾斜杠，POSIX 相对路径。 */
function normalizePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "");
}

/** 约定目录元信息；非约定目录返回 null（零噪音）。 */
export function stageDirMeta(path: string): StageDirMeta | null {
  const p = normalizePath(path);
  if (!p) return null;
  return STAGE_DIRS[p] ?? null;
}

/** 文件落在约定目录下时的小标签。 */
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

/** 「调研约定文档 · 3 件」副文案。 */
export function stageDirCaption(meta: StageDirMeta, fileCount: number): string {
  return `${meta.label} · ${fileCount} 件`;
}
