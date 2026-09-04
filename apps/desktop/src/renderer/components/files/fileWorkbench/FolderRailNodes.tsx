import type { FileSortBy } from "@/components/files/fileTreeTypes";
import { WorkspaceSection } from "@/components/files/fileWorkbench/WorkspaceSection";
import type { Tab } from "@/components/files/fileWorkbench/storage";
import type { FileSource } from "@/lib/fileSource";
import { type FolderTreeNode, childFolderNames } from "@/lib/folderTree";
import { type FolderMeta, isFolderOwner } from "@/services/folders";
import type { WorkspaceInfo } from "@/services/workspaces";
import type { ReactNode } from "react";

/** Everything a folder row needs from the workbench, passed once instead of drilled. */
export interface FolderRailHost {
  workspaceByWsId: Map<string, WorkspaceInfo>;
  sourceByWs: Map<string, FileSource | null>;
  expandedWs: Set<string>;
  onToggleWs: (wsId: string) => void;
  onOpenFile: (wsId: string, path: string, name: string) => void;
  activeTab: Tab | null;
  flashWsId: string | null;
  filterQuery: string;
  /** 树内兄弟排序（名称 / 大小 / 修改时间），中枢顶栏统一选、所有根共用。 */
  sortBy: FileSortBy;
  offline: boolean;
  /** 在此新建文件夹 — a real nested folder, not a bare `mkdir`. */
  onCreateSubfolder: (parent: FolderMeta, anchorEl?: Element | null) => void;
  /** Per-folder entries inside ``.agentcore``, when the host shows conventions. */
  renderWorkroomLead?: (folder: FolderMeta, indent: number) => ReactNode;
  /**
   * 「新建条目」on that folder's ``.agentcore`` header. Returning `false`
   * skips expanding the drawer after a failed create.
   */
  onCreateWorkroomEntry?: (folder: FolderMeta) => boolean | Promise<boolean>;
  revealWorkroomFolderId?: string | null;
  onWorkroomRevealApplied?: () => void;
}

/**
 * One folder row: header + its child folders + its files (``.agentcore`` is a
 * tree row, not a separate rail).
 *
 * A folder with no `/v1/workspaces` row yet (just created, list still stale) is
 * rendered from its {@link FolderMeta} so it never blinks out.
 */
export function FolderRailRow({
  node,
  folder,
  host,
}: {
  /** Tree node when the row nests; omit for a flat row (本机文件夹). */
  node?: FolderTreeNode;
  folder: FolderMeta;
  host: FolderRailHost;
}) {
  const wsId = `folder:${folder.id}`;
  const ws = host.workspaceByWsId.get(wsId) ?? folderWorkspaceFallback(folder);
  const children = node?.children ?? [];
  const createWorkroomEntry = host.onCreateWorkroomEntry;
  return (
    <WorkspaceSection
      ws={ws}
      depth={node?.depth ?? 0}
      showLocationBadge={false}
      hideRootDirs={node ? childFolderNames(node) : undefined}
      onCreateSubfolder={
        folder.mode === "cloud" && isFolderOwner(folder)
          ? (anchorEl) => host.onCreateSubfolder(folder, anchorEl)
          : undefined
      }
      source={host.sourceByWs.get(wsId) ?? null}
      offlineCloud={host.offline && ws.location === "cloud"}
      activePath={host.activeTab?.wsId === wsId ? host.activeTab.path : null}
      expanded={host.expandedWs.has(wsId)}
      onToggle={() => host.onToggleWs(wsId)}
      onOpenFile={(path, name) => host.onOpenFile(wsId, path, name)}
      flashing={wsId === host.flashWsId}
      filterQuery={host.filterQuery}
      sortBy={host.sortBy}
      renderWorkroomLead={
        host.renderWorkroomLead
          ? (indent) => host.renderWorkroomLead?.(folder, indent)
          : undefined
      }
      onCreateWorkroomEntry={
        createWorkroomEntry ? () => createWorkroomEntry(folder) : undefined
      }
      forceExpandWorkroom={host.revealWorkroomFolderId === folder.id}
      onWorkroomRevealApplied={host.onWorkroomRevealApplied}
      nested={
        children.length > 0 ? (
          <FolderRailNodes nodes={children} host={host} />
        ) : undefined
      }
    />
  );
}

/**
 * 我的文件 rendered as the real nested tree it now is (双模式工作区 §5.4):
 * each folder is a collapsible row whose child folders hang under it, and a
 * folder's own file tree sits below them — the same order an editor shows a
 * directory in.
 */
export function FolderRailNodes({
  nodes,
  host,
}: {
  nodes: FolderTreeNode[];
  host: FolderRailHost;
}) {
  return (
    <>
      {nodes.map((node) => (
        <FolderRailRow
          key={node.folder.id}
          node={node}
          folder={node.folder}
          host={host}
        />
      ))}
    </>
  );
}

/**
 * Stand-in workspace for a folder `/v1/workspaces` has not listed yet — a folder
 * created seconds ago must still be clickable, not wait out the list's refetch.
 */
export function folderWorkspaceFallback(folder: FolderMeta): WorkspaceInfo {
  return {
    wsId: `folder:${folder.id}`,
    name: folder.name,
    location: folder.mode === "local" ? "local" : "cloud",
    rootId: folder.localRootId,
    subpath: folder.localSubpath ?? "",
    hasFiles: false,
  };
}
