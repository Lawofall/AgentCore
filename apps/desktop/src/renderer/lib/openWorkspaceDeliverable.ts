import { hasInAppPreview } from "@/lib/capabilities";
import { baseName, isHtmlPath } from "@/lib/fileSource";
import { openWorkspaceHtmlInBrowser } from "@/lib/openWorkspaceHtmlInBrowser";
import { useSidePanelStore } from "@/stores/sidePanel";

/**
 * 终稿路径点击：HTML（且会话具备应用内预览）直达浏览器壳，其余开 File tab。
 */
export function openWorkspaceDeliverable(
  conversationId: string | null,
  path: string,
  workspaceId?: string | null,
): void {
  const trimmed = path.trim();
  if (!trimmed) return;
  if (conversationId && hasInAppPreview() && isHtmlPath(trimmed)) {
    void openWorkspaceHtmlInBrowser(
      conversationId,
      trimmed,
      workspaceId ?? undefined,
    );
    return;
  }
  useSidePanelStore
    .getState()
    .showFile(trimmed, baseName(trimmed) || trimmed, workspaceId);
}
