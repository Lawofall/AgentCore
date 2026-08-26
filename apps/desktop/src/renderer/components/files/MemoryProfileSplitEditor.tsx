/**
 * 「本文件夹记忆」的两栏编辑器：**同屏**编辑 *全局画像* 与 *当前文件夹画像*，并标注各自归属
 * （Agent记忆与知识系统 §1.6）。
 *
 * 为什么两栏而非单文件：注入时这两层是**叠加**的——全局画像对所有对话生效，文件夹画像只在
 * 这个文件夹内**附加**在全局之后（Agent记忆与知识系统 §二）。把两层并排摆出来，用户一眼看清
 * 「哪条是所有对话都记得的、哪条只此文件夹记得」。放错层时在对话「记忆已更新」卡片上搬层。
 *
 * 实现上不另造编辑器：左右各是一例 {@link MarkdownFileEditor}（`embedded` 隐去各自的返回键），
 * 分别指向全局 / 本文件夹的画像合成路径——读写 / CAS / 自动保存 / AI 改写全部照旧、互不串扰
 * （两层是不同文件，各自独立基线）。本壳只提供单一返回键 + 组合标题；各栏归属写在编辑器标题上。
 */

import { MarkdownFileEditor } from "@/components/files/MarkdownFileEditor";
import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import type { FileSource } from "@/lib/fileSource";
import {
  GLOBAL_PROFILE_PATH,
  memoryProjectProfilePath,
} from "@/services/sources/memorySource";
import { ChevronLeft, Layers } from "lucide-react";

export function MemoryProfileSplitEditor({
  source,
  folderId,
  folderName,
  onClose,
}: {
  /** The path-aware memory {@link FileSource}; each pane addresses a different leaf path. */
  source: FileSource;
  /** The project (= cloud workspace folderId) whose 画像 layer the right pane edits. */
  folderId: string;
  /** Display name of that project, for the 归属 label (falls back handled by caller). */
  folderName: string;
  onClose: () => void;
}) {
  const projectPath = memoryProjectProfilePath(folderId);

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border pl-1 pr-2.5">
        <SimpleTooltip label="返回文件列表">
          <IconButton onClick={onClose} aria-label="返回文件列表">
            <ChevronLeft size={16} />
          </IconButton>
        </SimpleTooltip>
        <Layers size={13} className="shrink-0 text-primary" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
          画像 · 全局 + 本文件夹
        </span>
        <span className="hidden shrink-0 text-xs text-muted-foreground xl:inline">
          注入时叠加：全局对所有对话生效，本文件夹仅在「{folderName}」内附加
        </span>
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col border-r border-border">
          <MarkdownFileEditor
            embedded
            source={source}
            path={GLOBAL_PROFILE_PATH}
            name="全局画像 · 所有对话共享"
            onClose={onClose}
          />
        </div>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <MarkdownFileEditor
            embedded
            source={source}
            path={projectPath}
            name={`本文件夹画像 · 仅「${folderName}」`}
            onClose={onClose}
          />
        </div>
      </div>
    </div>
  );
}
