import {
  type VersionSource,
  type VersionTimelineEntry,
  localVersionTimelineEntries,
  snapshotTimelineEntries,
} from "@/components/workspace/changesTimeline";
import { listLocalVersions } from "@/services/localWorkspaceVersions";
import { wsListSnapshots } from "@/services/workspaces";
import { useCallback, useEffect, useState } from "react";

/**
 * 文件页「版本」面板的列表来源 —— 云端 REST 或本机 sidecar，由 {@link VersionSource} 分流。
 * 右坞「改动」tab 不再拉版本轨。
 *
 * 调用方须 memo 化 `source`：它就是重拉的依赖。
 */
export function useChangesVersions(source: VersionSource | null): {
  entries: VersionTimelineEntry[];
  failed: boolean;
  reload: () => Promise<void>;
} {
  const [entries, setEntries] = useState<VersionTimelineEntry[]>([]);
  const [failed, setFailed] = useState(false);

  const reload = useCallback(async () => {
    if (!source) {
      setEntries([]);
      setFailed(false);
      return;
    }
    try {
      if (source.origin === "localWs") {
        setEntries(
          localVersionTimelineEntries(
            await listLocalVersions({
              rootId: source.rootId,
              subpath: source.subpath,
            }),
          ),
        );
      } else {
        setEntries(snapshotTimelineEntries(await wsListSnapshots(source.wsId)));
      }
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [source]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { entries, failed, reload };
}
