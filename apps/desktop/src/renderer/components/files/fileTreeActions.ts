import type { FileTreeHandle } from "./fileTreeTypes";

/**
 * 「在某个工作区根上做点什么」——外层容器（工作区分节）的右键菜单与
 * 工具栏都只表达意图，真正的动作由树自己经 {@link FileTreeHandle} 执行。
 *
 * 抽出来是因为这些容器**折叠时树还没挂载**：它们都得先记下意图、展开后补跑同一段分派。
 * 各写一份的结果就是新增一个动作（如上传文件夹）时漏掉其中一处。
 */
export type TreeAction = "file" | "dir" | "upload" | "upload-folder";

export function runTreeAction(
  tree: FileTreeHandle | null,
  action: TreeAction,
): void {
  if (action === "upload") tree?.triggerUpload();
  else if (action === "upload-folder") tree?.triggerUploadFolder();
  else tree?.startCreate(action);
}
