import { EmptyHint } from "@/components/files/parts";
import { Button } from "@/components/ui";
import { ChangesVersionEntry } from "@/components/workspace/ChangesVersionEntry";
import { KeepVersionAction } from "@/components/workspace/KeepVersionAction";
import type { VersionSource } from "@/components/workspace/changesTimeline";
import { useChangesVersions } from "@/components/workspace/useChangesVersions";
import { History } from "lucide-react";
import { useMemo } from "react";

/**
 * 文件页的「版本」面板 —— 一个云端工作区的留存版本与交接存档，倒序排列。
 *
 * 右坞「改动」tab 不再列版本；命名版本只从这里创建 / 恢复 / 下载。
 * 没有会话就谈不上「回合 N」，所以这里只排版本，不假装能给出某个回合的逐文件 diff。
 */
export function WorkspaceVersionsPanel({
  wsId,
  name,
}: {
  wsId: string;
  /** 工作区显示名，用于空态里指名道姓。 */
  name: string;
}) {
  const source = useMemo<VersionSource>(
    () => ({ origin: "cloudWs", wsId }),
    [wsId],
  );
  const { entries, failed, reload } = useChangesVersions(source);
  // 文件夹工作区（folder:*）的版本按存储键组织，同文件夹的各会话共用一份历史。
  const shared = wsId.startsWith("folder:");

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <History size={13} className="shrink-0 text-muted-foreground" />
        <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
          「{name}」的版本
        </p>
        <KeepVersionAction source={source} onCreated={() => void reload()} />
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {failed ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>版本没能加载出来。</span>
            <Button variant="ghost" onClick={() => void reload()}>
              重试
            </Button>
          </div>
        ) : null}

        {entries.length === 0 && !failed ? (
          <EmptyHint
            inline
            icon={<History size={26} className="text-muted-foreground/40" />}
            title="暂无版本"
          />
        ) : (
          entries.map((entry) => (
            <ChangesVersionEntry
              key={entry.id}
              source={source}
              entry={entry}
              shared={shared}
              onChanged={() => void reload()}
            />
          ))
        )}
      </div>
    </div>
  );
}
