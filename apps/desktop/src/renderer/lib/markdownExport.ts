import { notifyFileTreeChanged } from "@/components/files/fileTreeBus";
import {
  type DocLayout,
  type FileSource,
  type MarkdownExportFormat,
  baseName,
  isMarkdownPath,
  parentDir,
} from "@/lib/fileSource";
import { notifyActionError, notifySuccess } from "@/lib/toast";

/** 可写源 + Markdown + 转换器在场，才挂导出入口。 */
export function canExportMarkdown(source: FileSource, path: string): boolean {
  return source.caps.edit && isMarkdownPath(path) && !!source.exportMarkdown;
}

/** 人侧导出：写同目录同名件，并通知已挂载的文件树补丁。 */
export async function exportWorkspaceMarkdown(
  source: FileSource,
  path: string,
  format: MarkdownExportFormat,
  layout: DocLayout,
): Promise<void> {
  try {
    const result = await source.exportMarkdown?.(path, format, layout);
    if (!result) return;
    notifyFileTreeChanged({ sourceId: source.id, dir: parentDir(path) });
    const name = baseName(result.path);
    if (result.warnings.length > 0) {
      notifySuccess(`已导出 ${name}（${result.warnings.length} 条警告）`);
    } else {
      notifySuccess(`已导出 ${name}`);
    }
  } catch (e) {
    notifyActionError(format === "pdf" ? "导出 PDF 失败" : "导出 Word 失败", e);
  }
}
