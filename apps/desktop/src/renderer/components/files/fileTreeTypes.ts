/**
 * 剪贴板内容：一批待复制/剪切的源内路径 + 操作类型（单选即长度为 1）+ 来源树。
 *
 * `sourceId` 让粘贴方认得出「这份是别的树剪来的」，据此走跨源搬运（见
 * `fileTreeTransfer.ts`）；剪贴板本身全局只有一份（见 `fileClipboard.ts`），
 * 否则在 A 文件夹剪切就永远只能粘回 A。
 */
export type ClipboardEntry = {
  op: "copy" | "cut";
  sourceId: string;
  paths: string[];
};

/**
 * 右键落在多选选区内时，菜单改为对**整批**生效的动作集（`null` = 这一行不在多选里，走单项菜单）。
 * 批量移动不在此列：目标目录靠既有的「剪切 → 粘贴到此文件夹」表达，不另造目标选择面。
 */
export interface BatchMenuActions {
  count: number;
  /** 选区里能下载的项数（文件另存；文件夹整夹 zip）。 */
  downloadableCount: number;
  onDownload: () => void;
  onCut: () => void;
  onDelete: () => void;
}

/**
 * 兄弟排序依据。目录仍恒在文件之前（文件管理器通行做法，也让「展开哪一层」保持稳定），
 * 排序只决定同档内的先后：名称升序 / 修改时间降序（新的在前）。缺 mtime 的条目沉到该档
 * 末尾，再按名称排，免得源不报时间时顺序看起来是随机的。
 */
export type FileSortBy = "name" | "mtime";

export interface FileTreeHandle {
  /** 由外层（如多根工作区的根节点右键菜单）触发的「在源根处内联新建」。 */
  startCreate: (kind: "file" | "dir") => void;
  /** 刷新根 + 所有已展开目录。 */
  refresh: () => void;
  /** 打开 OS 文件选择器，上传到源根（仅可传输的源）。 */
  triggerUpload: () => void;
  /** 打开 OS **目录**选择器，把整个文件夹（含层级）上传到源根。 */
  triggerUploadFolder: () => void;
  /** 收起全部展开目录（外置工具栏的「全部折叠」用）。 */
  collapseAll: () => void;
}

/** 树内部「工具栏相关」的活动状态，供外置工具栏（如侧栏面板头）响应式渲染。 */
export interface FileTreeChromeState {
  /** 正在上传（上传按钮转圈/禁用）。 */
  uploading: boolean;
  /** 有已展开目录（决定是否显示「全部折叠」）。 */
  hasExpanded: boolean;
  /** 根正在加载（刷新按钮转圈）。 */
  loading: boolean;
}
