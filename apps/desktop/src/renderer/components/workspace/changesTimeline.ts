/**
 * 文件页「版本」面板的条目模型 —— 用户留存版本 + 交接存档。
 * 右坞「改动」tab 不再列版本（只审回合 diff / 基线回滚）。
 *
 * 不单列自动备份（label 为空）与 `turn-baseline:`：回合 N 的结束态与回合 N+1 的
 * 基线是同一个时间点，「改动」tab 的回合条目已经代表它。可见性过滤复用
 * {@link visibleSnapshots}（它另外挡掉导出 / 预览这类传输副产物）。
 */

import {
  classifySnapshotLabel,
  snapshotDisplayHint,
  snapshotDisplayTitle,
  visibleSnapshots,
} from "@/components/workspace/snapshotDisplay";
import type { WorkspaceVersion } from "@/services/localWorkspaceVersions";
import type { WorkspaceSnapshot } from "@/services/workspace";
import type { WorkspaceInfo } from "@/services/workspaces";

/**
 * 版本住在哪里。云端走工作区快照 REST；本机走 sidecar / 盘上 `AgentCore/versions`。
 * 同一块「版本」面板，两条后端，不拿云端 REST 去打本机（409）。
 */
export type VersionSource =
  | { origin: "cloudWs"; wsId: string }
  | { origin: "localWs"; rootId: string; subpath: string };

/** 文件页当前这张桌 → 版本源；本机根还没绑上则没有入口。 */
export function versionSourceFromWorkspace(
  ws: Pick<WorkspaceInfo, "location" | "wsId" | "rootId" | "subpath">,
): VersionSource | null {
  if (ws.location === "cloud") return { origin: "cloudWs", wsId: ws.wsId };
  if (ws.rootId) {
    return { origin: "localWs", rootId: ws.rootId, subpath: ws.subpath };
  }
  return null;
}

/** 用户主动打的锚点（version）或系统交接存档（archive）。 */
export interface VersionTimelineEntry {
  kind: "version" | "archive";
  /** 云端 snapshotId —— 下载、恢复都用它寻址。 */
  id: string;
  /** 卡片标题（系统 label 已归一为人话）。 */
  title: string;
  /** 标题被改写时的原始 label，只进 tooltip。 */
  rawLabel: string | null;
  sizeBytes: number;
  at: string;
}

/** 云端快照列表 → 可见条目：只留用户版本与交接存档。 */
export function snapshotTimelineEntries(
  snaps: readonly WorkspaceSnapshot[],
): VersionTimelineEntry[] {
  const out: VersionTimelineEntry[] = [];
  for (const snapshot of visibleSnapshots(snaps)) {
    const kind = classifySnapshotLabel(snapshot.label);
    if (kind === "auto") continue;
    out.push({
      kind: kind === "kept" ? "version" : "archive",
      id: snapshot.snapshotId,
      title: snapshotDisplayTitle(snapshot.label),
      rawLabel: snapshotDisplayHint(snapshot.label),
      sizeBytes: snapshot.sizeBytes,
      at: snapshot.createdAt,
    });
  }
  return out;
}

/** 本机命名版本列表 → 可见条目（没有交接存档这一档）。 */
export function localVersionTimelineEntries(
  versions: readonly WorkspaceVersion[],
): VersionTimelineEntry[] {
  return versions.map((version) => ({
    kind: "version",
    id: version.versionId,
    title: version.name,
    rawLabel: null,
    sizeBytes: version.sizeBytes,
    at: version.createdAt,
  }));
}
