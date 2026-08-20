import { FileWorkbench } from "@/components/files/FileWorkbench";
import { useWorkspaces } from "@/hooks/useWorkspaces";
import { hasLocalFiles } from "@/lib/capabilities";
import { useMemo } from "react";
import { useLocation } from "react-router-dom";

/**
 * The 文件 hub (跨工作区文件总览) — one place to browse files across folder and
 * shared-space roots (`folder:<id>` + `shared:<id>`，云 + 本地) without first
 * opening a conversation. Layout is VSCode 式左树右详情: the left rail stacks
 * 我的文件（嵌套树）/ 本机文件夹 / 共享空间 as collapsible sections over their
 * own {@link FileSource}. `conv:` scratch is not a hub zone — 裸聊写盘进自动建桌.
 *
 * 文件夹（Folder）生命周期删除入口在本页各 `folder:` 根的右键菜单；对话列表页
 * `/conversations` 只做对话归档/删除。「浏览文件」jumps here with
 * `focusWsId`（`folder:<id>`）so the target section expands + highlights.
 */
export function FilesPage() {
  const location = useLocation();
  const query = useWorkspaces();
  const workspaces = useMemo(() => query.data ?? [], [query.data]);

  const focusWsId =
    (location.state as { focusWsId?: string } | null)?.focusWsId ?? null;

  const openMemoryLeaf =
    (
      location.state as {
        openMemoryLeaf?: {
          path: string;
          name: string;
          projectId?: string | null;
        };
      } | null
    )?.openMemoryLeaf ?? null;

  return (
    <FileWorkbench
      workspaces={workspaces}
      isLoading={query.isLoading}
      isError={query.isError}
      onRetry={() => void query.refetch()}
      fsAvailable={hasLocalFiles()}
      showMemory
      focusWsId={focusWsId}
      openMemoryLeaf={openMemoryLeaf}
      focusKey={location.key}
    />
  );
}
