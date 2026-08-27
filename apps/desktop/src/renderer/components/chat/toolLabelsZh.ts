/**
 * 中文工具展示名 —— 审批卡 / 委派授权共用。
 * 与 stores/execution/types.toolLabel（英文图节点进度文案）分表维护，勿混成一套。
 */
export const TOOL_LABELS_ZH: Record<string, string> = {
  file_write: "写入文件",
  file_append: "追加文件",
  str_replace: "修改文件",
  file_delete: "删除文件",
  file_move: "移动文件",
  file_copy: "复制文件",
  mkdir: "创建目录",
  file_batch: "批量文件操作",
  code_execute: "执行代码",
  test_run: "运行测试",
  git: "Git 写入",
  browser: "浏览器",
  host: "本机 Host",
  terminal: "终端",
  desktop_notify: "系统通知",
  external_mount_readonly: "挂载本机目录",
  delete_folder: "删除文件夹",
};

export function toolLabelZh(name: string): string {
  return TOOL_LABELS_ZH[name] ?? name;
}
