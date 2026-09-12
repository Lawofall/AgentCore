import { Button } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { hasAutoUpdater } from "@/lib/capabilities";
import { clientVersion } from "@/lib/clientBuildInfo";
import { formatBytes, formatDownloadProgress } from "@/lib/format";
import {
  clientReleaseChannel,
  desktopDownloadUrlForChannel,
} from "@/lib/releaseChannel";
import {
  UPDATE_NOTES_FALLBACK,
  isForceUpdateActive,
  useUpdatesStore,
} from "@/stores/updates";
import { Loader2 } from "lucide-react";

/**
 * Consent-first update explanation dialog (发布与门禁.md §7.6).
 *
 * Soft update: only the `available` consent surface — 「下载安装包」关窗并后台下载
 * 到系统「下载」文件夹。Force-update hard gate: non-dismissible multi-phase
 * （进度 / 打开安装包 / 重试）；任意 phase 都保留下载页作为失败出口。
 */
export function UpdateAvailableDialog() {
  const dialogOpen = useUpdatesStore((s) => s.dialogOpen);
  const status = useUpdatesStore((s) => s.status);
  const outdatedMinVersion = useUpdatesStore((s) => s.outdatedMinVersion);
  const closeUpdateDialog = useUpdatesStore((s) => s.closeUpdateDialog);
  const download = useUpdatesStore((s) => s.download);
  const remindLater = useUpdatesStore((s) => s.remindLater);
  const skipVersion = useUpdatesStore((s) => s.skipVersion);
  const install = useUpdatesStore((s) => s.install);

  if (!hasAutoUpdater()) return null;

  const force = isForceUpdateActive({ outdatedMinVersion });

  const version =
    status.phase === "available" ||
    status.phase === "downloading" ||
    status.phase === "downloaded"
      ? status.version
      : null;

  // Soft: consent only. Force: keep download / ready / error in-dialog.
  const relevant = force
    ? status.phase === "available" ||
      status.phase === "downloading" ||
      status.phase === "downloaded" ||
      status.phase === "error"
    : status.phase === "available";

  const open = dialogOpen && relevant;

  const releaseNotes =
    status.phase === "available"
      ? status.releaseNotes?.trim() || UPDATE_NOTES_FALLBACK
      : UPDATE_NOTES_FALLBACK;

  const sizeBytes =
    status.phase === "available" ? (status.sizeBytes ?? null) : null;

  const downloadPageUrl = desktopDownloadUrlForChannel(clientReleaseChannel());

  const current = clientVersion();
  const title =
    status.phase === "downloaded"
      ? `安装包 ${version} 已下载`
      : status.phase === "downloading"
        ? `正在下载 ${version}`
        : status.phase === "error"
          ? "更新失败"
          : `发现新版本 ${version ?? ""}`;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !force) closeUpdateDialog();
      }}
    >
      {relevant ? (
        <DialogContent
          size="md"
          className="flex max-h-[min(80vh,32rem)] flex-col gap-0 p-0"
          showClose={!force}
          onEscapeKeyDown={(e) => {
            if (force) e.preventDefault();
          }}
          onPointerDownOutside={(e) => {
            if (force) e.preventDefault();
          }}
          onInteractOutside={(e) => {
            if (force) e.preventDefault();
          }}
        >
          <DialogHeader className={force ? undefined : "pr-10"}>
            <DialogTitle>{title}</DialogTitle>
          </DialogHeader>

          <DialogBody className="flex-1 space-y-3 pb-2">
            <DialogDescription asChild>
              <div className="space-y-3">
                {status.phase === "available" ? (
                  <>
                    <p className="text-sm text-muted-foreground">
                      当前版本 {current}
                      {sizeBytes != null && sizeBytes > 0
                        ? ` · 安装包约 ${formatBytes(sizeBytes)}`
                        : null}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      将下载安装包到本机「下载」文件夹，打开后按向导完成安装。
                    </p>
                    <p className="whitespace-pre-wrap text-sm text-foreground">
                      {releaseNotes}
                    </p>
                  </>
                ) : null}

                {force && status.phase === "downloading" ? (
                  <div className="space-y-2">
                    <p className="text-sm text-muted-foreground">
                      下载进度{" "}
                      {formatDownloadProgress({
                        percent: status.percent,
                        transferred: status.transferred,
                        total: status.total,
                        bytesPerSecond: status.bytesPerSecond,
                      })}
                    </p>
                    <progress
                      className="h-2 w-full overflow-hidden rounded-full bg-muted [&::-webkit-progress-bar]:bg-muted [&::-webkit-progress-value]:bg-primary [&::-moz-progress-bar]:bg-primary"
                      value={Math.min(100, status.percent)}
                      max={100}
                    />
                  </div>
                ) : null}

                {force && status.phase === "downloaded" ? (
                  <p className="text-sm text-muted-foreground">
                    安装包已保存到本机「下载」文件夹，打开后按向导完成安装。
                  </p>
                ) : null}

                {force && status.phase === "error" ? (
                  <p className="text-sm text-muted-foreground">
                    {status.message}
                  </p>
                ) : null}
              </div>
            </DialogDescription>
          </DialogBody>

          <DialogFooter>
            {status.phase === "available" ? (
              <>
                {force ? null : (
                  <>
                    <Button
                      variant="outline"
                      size="md"
                      onClick={() => skipVersion()}
                    >
                      跳过此版本
                    </Button>
                    <Button
                      variant="outline"
                      size="md"
                      onClick={() => remindLater()}
                    >
                      稍后提醒
                    </Button>
                  </>
                )}
                <Button
                  variant="primary"
                  size="md"
                  onClick={() => void download()}
                >
                  下载安装包
                </Button>
              </>
            ) : null}

            {force && status.phase === "downloading" ? (
              <>
                <Button
                  variant="neutral"
                  size="md"
                  disabled
                  icon={<Loader2 size={14} className="animate-spin" />}
                >
                  下载中…
                </Button>
                <a
                  href={downloadPageUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-8 items-center justify-center rounded-lg px-3 text-xs font-medium text-foreground underline-offset-2 hover:underline"
                >
                  前往下载页手动安装
                </a>
              </>
            ) : null}

            {force && status.phase === "downloaded" ? (
              <Button
                variant="primary"
                size="md"
                onClick={() => void install()}
              >
                打开安装包
              </Button>
            ) : null}

            {force && status.phase === "error" ? (
              <>
                <Button
                  variant="primary"
                  size="md"
                  onClick={() => void download()}
                >
                  重试下载
                </Button>
                <a
                  href={downloadPageUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-8 items-center justify-center rounded-lg px-3 text-xs font-medium text-foreground underline-offset-2 hover:underline"
                >
                  前往下载页手动安装
                </a>
              </>
            ) : null}
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
