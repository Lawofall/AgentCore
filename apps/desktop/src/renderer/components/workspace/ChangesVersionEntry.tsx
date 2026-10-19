import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import type {
  VersionSource,
  VersionTimelineEntry,
} from "@/components/workspace/changesTimeline";
import { formatBytes, formatMessageTime } from "@/lib/format";
import { notifyActionError, notifyError } from "@/lib/toast";
import { restoreLocalVersion } from "@/services/localWorkspaceVersions";
import { wsDownloadSnapshot, wsRestoreSnapshot } from "@/services/workspaces";
import { Archive, Bookmark, Download, Loader2, RotateCcw } from "lucide-react";
import { useState } from "react";

/**
 * 文件页版本列表上的一条 —— 用户留存版本 / 交接存档。
 * 云端有 download、没有 delete；本机 zip 已在盘上，只提供恢复。
 */

/** 恢复的能力边界（overlay 覆盖，不是完整镜像）。 */
const restoreHint = (what: string) =>
  `尽最大努力回到${what}（overlay 覆盖同名文件；此后新建的文件不会被删除，未进包的目录如 node_modules/.venv 等也不会还原）`;

/** 版本按工作区存储键组织，文件夹下各对话共用一份历史——不是 bug，说清即可。 */
const SHARED_HINT =
  "版本存在这个文件夹里：同一文件夹的其他对话也看得到、也能恢复。";

export function ChangesVersionEntry({
  source,
  entry,
  shared,
  onChanged,
}: {
  source: VersionSource;
  entry: VersionTimelineEntry;
  /** 工作区属于某个项目（`folder:*`）时，版本对兄弟会话可见。 */
  shared: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<"download" | "restore" | null>(null);
  const isVersion = entry.kind === "version";
  const what = isVersion ? "这个版本" : "这份存档";
  const canDownload = source.origin === "cloudWs";

  const onDownload = async () => {
    if (busy || source.origin !== "cloudWs") return;
    setBusy("download");
    try {
      await wsDownloadSnapshot(source.wsId, entry.id);
    } catch (e) {
      notifyActionError("下载失败", e);
    } finally {
      setBusy(null);
    }
  };

  const onRestore = async () => {
    if (busy) return;
    if (!window.confirm(`${restoreHint(what)}。确定继续？`)) return;
    setBusy("restore");
    try {
      if (source.origin === "localWs") {
        await restoreLocalVersion(
          { rootId: source.rootId, subpath: source.subpath },
          entry.id,
        );
      } else {
        await wsRestoreSnapshot(source.wsId, entry.id);
      }
      onChanged();
    } catch (e) {
      notifyError(e, "恢复失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section
      data-testid="changes-timeline-entry"
      data-entry-kind={entry.kind}
      data-entry-id={entry.id}
      className={`rounded-xl border ${
        isVersion ? "border-primary/40 bg-primary/5" : "border-border bg-card"
      }`}
    >
      <header className="flex items-center gap-2 px-3 py-2">
        {isVersion ? (
          <Bookmark size={13} className="shrink-0 text-primary" />
        ) : (
          <Archive size={13} className="shrink-0 text-muted-foreground" />
        )}
        <h3
          className={`min-w-0 flex-1 truncate text-xs font-medium ${
            isVersion ? "text-foreground" : "text-muted-foreground"
          }`}
          title={entry.rawLabel ?? entry.title}
        >
          {entry.title}
        </h3>
        {canDownload ? (
          <SimpleTooltip label={`下载${what} (zip)`}>
            <IconButton
              disabled={busy === "download"}
              onClick={() => void onDownload()}
              aria-label={`下载${what} (zip)`}
            >
              {busy === "download" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Download size={13} />
              )}
            </IconButton>
          </SimpleTooltip>
        ) : null}
        <SimpleTooltip label={restoreHint(what)}>
          <IconButton
            disabled={busy === "restore"}
            onClick={() => void onRestore()}
            aria-label={`恢复到${what}`}
          >
            {busy === "restore" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <RotateCcw size={13} />
            )}
          </IconButton>
        </SimpleTooltip>
      </header>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 pb-2 text-xs text-muted-foreground">
        <span>{isVersion ? "留存版本" : "交接存档"}</span>
        <span>·</span>
        <span>{formatMessageTime(entry.at)}</span>
        <span>·</span>
        <span>{formatBytes(entry.sizeBytes)}</span>
        {isVersion && shared ? (
          <>
            <span>·</span>
            <SimpleTooltip label={SHARED_HINT}>
              <span className="underline decoration-dotted underline-offset-2">
                本项目共享
              </span>
            </SimpleTooltip>
          </>
        ) : null}
      </div>
    </section>
  );
}
