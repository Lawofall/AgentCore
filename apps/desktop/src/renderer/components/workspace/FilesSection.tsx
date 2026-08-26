import type { FileSource } from "@/lib/fileSource";
import type { ReactNode } from "react";
import { FileBrowser } from "./FileBrowser";

/**
 * 对话工作区的文件面板 = 共用 {@link FileBrowser} 的 n=1 实例（项目即工作区）：
 * 树构建 / 增删改 / 拖拽 / 上传 / 预览全部下沉到 FileBrowser，与文件中枢页共用。
 * 「按对话选源」上移到 {@link WorkspaceMode}（经 `useConversationWorkspace` +
 * `resolveWorkspaceSource`，与文件中枢同一份数据/解析器，故云端/本地一致不漂移），这里只
 * 直透已解析的 `source`。`leading` / `trailing` 直透给 FileBrowser 的单行工具栏（云端选择器 /
 * 快照）。`source` 为 null 时（本地源在本机不可用）由 FileBrowser 兜空态。
 */
export function FilesSection({
  source,
  leading,
  trailing,
  emptyTreeHint,
  onCloneGit,
  renderWorkroomLead,
  onCreateWorkroomEntry,
}: {
  source: FileSource | null;
  leading?: ReactNode;
  trailing?: ReactNode;
  emptyTreeHint?: string;
  onCloneGit?: () => void;
  renderWorkroomLead?: (indent: number) => ReactNode;
  onCreateWorkroomEntry?: () => boolean | Promise<boolean>;
}) {
  return (
    <FileBrowser
      source={source}
      leading={leading}
      trailing={trailing}
      emptyTreeHint={emptyTreeHint}
      onCloneGit={onCloneGit}
      renderWorkroomLead={renderWorkroomLead}
      onCreateWorkroomEntry={onCreateWorkroomEntry}
    />
  );
}
