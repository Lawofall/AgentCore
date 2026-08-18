/**
 * 约定文档约定目录（``AgentCore/文档/{工作稿,research,debate,reviews}/``）的中性元信息——
 * 文件树徽章与产物卡标签共用。与后端 ``workspace.stage_dirs`` 对齐；无匹配则零噪音。
 * 约定根本身的呈现名（「AI 工作间」）也在这里，同属「按路径给盘上目录配文案」。
 */

/** 盘上约定根目录名——与后端 ``stage_dirs.AGENTCORE_ROOT`` 对齐（磁盘真名，勿改）。 */
export const AGENTCORE_ROOT = "AgentCore";
export const DOCS_PREFIX = `${AGENTCORE_ROOT}/文档`;
export const DRAFTS_DIR = `${DOCS_PREFIX}/工作稿`;
export const RESEARCH_DIR = `${DOCS_PREFIX}/research`;
export const DEBATE_DIR = `${DOCS_PREFIX}/debate`;
export const REVIEWS_DIR = `${DOCS_PREFIX}/reviews`;

/**
 * 工作区根下 ``AgentCore/`` 的**呈现名**（双模式工作区 §四「呈现层的统一入口已推翻」）：
 * 这里装的是 AI 干活留下的过程材料——故次要呈现、钉在同级最前（侧栏窄视口够得着），且与
 * 条目区（「全局设定」/「本文件夹设定」）不再同名。仅改显示：磁盘路径 / 后端常量 / stage_dirs 一律不动。
 */
export const AGENTCORE_ROOT_LABEL = "AI 工作间";

export const AGENTCORE_ROOT_TOOLTIP = `AI 干活留下的过程材料（${AGENTCORE_ROOT}/）；成品会归位到工作区`;

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
