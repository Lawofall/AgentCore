import { BrandMarkIcon } from "@/components/brand/BrandMark";
import {
  SettingRow,
  SettingsAsync,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import { Button, Card } from "@/components/ui";
import {
  checkAndroidUpdate,
  openAndroidDownload,
  useAndroidUpdates,
} from "@/lib/androidUpdates";
import { hasAutoUpdater, isWebRuntime } from "@/lib/capabilities";
import {
  clientGitSha,
  clientVersion,
  formatGitSha,
} from "@/lib/clientBuildInfo";
import { formatDownloadProgress } from "@/lib/format";
import {
  clientChannelLabelZh,
  clientReleaseChannel,
  desktopDownloadUrlForChannel,
  otherChannelDownloadLabel,
  otherChannelDownloadUrl,
} from "@/lib/releaseChannel";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { type VersionInfo, fetchVersion } from "@/services/system";
import { useUpdatesStore } from "@/stores/updates";
import type { UpdaterStatus } from "@shared/updater-contract";
import { Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { SettingsHeader } from "./SettingsHeader";

/** Human-readable line for each updater phase (发布与门禁.md §7.6). */
function updateStatusText(status: UpdaterStatus): string {
  switch (status.phase) {
    case "idle":
      return "点击下方按钮检查是否有新版本。";
    case "unsupported":
      return "开发模式下不检查更新；自动更新仅在安装版中生效。";
    case "checking":
      return "正在检查更新…";
    case "not-available":
      return "已是最新版本。";
    case "available":
      return `发现新版本 ${status.version}，确认后下载安装包。`;
    case "downloading":
      return `正在下载安装包 ${status.version}…（${formatDownloadProgress({
        percent: status.percent,
        transferred: status.transferred,
        total: status.total,
        bytesPerSecond: status.bytesPerSecond,
      })}）`;
    case "downloaded":
      return `安装包 ${status.version} 已下载，打开后按向导完成安装。`;
    case "error":
      return `更新失败：${status.message}`;
  }
}

function AndroidUpdateSection() {
  const status = useAndroidUpdates();
  const busy = status.phase === "checking";
  return (
    <SettingsSection
      title="软件更新"
      description={status.message ?? "检查 Android 安装包是否有新版本。"}
      divider
    >
      <div className="flex flex-wrap items-center gap-2">
        {status.phase === "available" ? (
          <Button size="md" onClick={() => openAndroidDownload()}>
            去下载
          </Button>
        ) : null}
        <Button
          variant="neutral"
          size="md"
          disabled={busy || status.phase === "unsupported"}
          icon={
            busy ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <RefreshCw size={14} />
            )
          }
          onClick={() => void checkAndroidUpdate()}
        >
          检查更新
        </Button>
      </div>
    </SettingsSection>
  );
}

/** 软件更新: mirror the main-process updater status + 检查 / 查看 / 打开安装包. */
function UpdateSection() {
  const status = useUpdatesStore((s) => s.status);
  const check = useUpdatesStore((s) => s.check);
  const install = useUpdatesStore((s) => s.install);
  const openUpdateDialog = useUpdatesStore((s) => s.openUpdateDialog);

  const busy = status.phase === "checking" || status.phase === "downloading";
  const showDownloadPageLink = status.phase === "error";
  const downloadPageUrl = desktopDownloadUrlForChannel(clientReleaseChannel());

  return (
    <SettingsSection
      title="软件更新"
      description={updateStatusText(status)}
      divider
    >
      {status.phase === "downloading" ? (
        <div className="space-y-2">
          <progress
            className="h-2 w-full overflow-hidden rounded-full bg-muted [&::-webkit-progress-bar]:bg-muted [&::-webkit-progress-value]:bg-primary [&::-moz-progress-bar]:bg-primary"
            value={Math.min(100, status.percent)}
            max={100}
          />
          <Button
            variant="neutral"
            size="md"
            disabled
            icon={<Loader2 size={14} className="animate-spin" />}
          >
            下载中…
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {status.phase === "downloaded" ? (
            <Button size="md" onClick={() => void install()}>
              打开安装包
            </Button>
          ) : null}
          {showDownloadPageLink ? (
            <a
              href={downloadPageUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-8 items-center justify-center rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90"
            >
              前往下载页
            </a>
          ) : null}
          {status.phase === "available" ? (
            <Button size="md" onClick={() => openUpdateDialog()}>
              查看更新
            </Button>
          ) : null}
          {status.phase !== "downloaded" && status.phase !== "available" ? (
            <Button
              variant="neutral"
              size="md"
              disabled={busy || status.phase === "unsupported"}
              icon={
                busy ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <RefreshCw size={14} />
                )
              }
              onClick={() => void check()}
            >
              检查更新
            </Button>
          ) : null}
          {status.phase === "available" ? (
            <Button
              variant="neutral"
              size="md"
              icon={<RefreshCw size={14} />}
              onClick={() => void check()}
            >
              重新检查
            </Button>
          ) : null}
        </div>
      )}
    </SettingsSection>
  );
}

/**
 * 品牌区 — 只留产品标记与两行 slogan。
 *
 * 原来这里是 `BrandMark`（含 text-xl 字标）+ 一行 text-base slogan 摞在页头之上，
 * 与 `SettingsHeader` 的 h1 同级同字号，看上去是两个并列大标题。产品名由页头
 * 「关于 AgentCore」承载，这里降级成一张说明卡：图标 + 定位语。
 */
function BrandCard() {
  return (
    <Card className="flex items-center gap-4 px-4 py-4">
      <BrandMarkIcon size={36} title="AgentCore" />
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">
          协作，是更高级的智能。
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">协作智能平台</p>
      </div>
    </Card>
  );
}

/** 版本溯源表：标签左、值右。原来是固定 `w-20` 标签列，「API 构建时间」这类
 *  四字以上标签会被挤成两行。 */
function VersionSection() {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchVersion();
        if (!cancelled) setInfo(data);
      } catch {
        if (!cancelled) setError("获取版本信息失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const gitSha = clientGitSha();

  return (
    <SettingsSection title="版本与构建" contentClassName="space-y-3">
      <Card>
        <SettingRow surface="list" label="客户端版本" value={clientVersion()} />
        <SettingRow
          surface="list"
          divider
          label="客户端构建"
          value={
            <span className={gitSha !== "unknown" ? "font-mono" : undefined}>
              {formatGitSha(gitSha)}
            </span>
          }
        />
        {/* 桌面双轨：构建期通道；web 无并列安装身份，不展示。 */}
        {!isWebRuntime() ? (
          <SettingRow
            surface="list"
            divider
            label="更新通道"
            value={clientChannelLabelZh()}
          />
        ) : null}
        <SettingsAsync
          loading={loading}
          error={error}
          size="sm"
          className="border-t border-border px-4 py-2.5"
        >
          {info ? (
            <>
              <SettingRow
                surface="list"
                divider
                label="API 版本"
                value={info.version}
              />
              <SettingRow
                surface="list"
                divider
                label="API 构建"
                value={
                  <span
                    className={
                      info.gitSha !== "unknown" ? "font-mono" : undefined
                    }
                  >
                    {formatGitSha(info.gitSha)}
                  </span>
                }
              />
              <SettingRow
                surface="list"
                divider
                label="API 构建时间"
                value={info.builtAt === "unknown" ? "—" : info.builtAt}
              />
            </>
          ) : null}
        </SettingsAsync>
      </Card>

      {/* 桌面：链到另一轨官网下载页（外链；不做同装热切 feed）。 */}
      {!isWebRuntime() ? (
        <p className="text-sm">
          <a
            href={otherChannelDownloadUrl()}
            target="_blank"
            rel="noopener noreferrer"
            className="text-foreground underline-offset-2 hover:underline"
          >
            {otherChannelDownloadLabel()}
          </a>
        </p>
      ) : null}
    </SettingsSection>
  );
}

/**
 * 关于（/more/about）— 品牌、版本溯源、软件更新、法律与合规。
 *
 * 开发者 / 诊断模式与「允许本机执行」原本挂在本页（挨着构建溯源），用户找不到，
 * 已搬到「通用」（/more/general）的「进阶」区。
 */
export function AboutSettings() {
  return (
    <div>
      <SettingsHeader
        title="关于 AgentCore"
        description="版本信息、软件更新与法律条款。"
      />

      <SettingsStack>
        <BrandCard />

        <VersionSection />

        {/* 自动更新仅桌面外壳；web 客户端随刷新拿到新版，故 web 不挂「软件更新」。 */}
        {hasAutoUpdater() && <UpdateSection />}
        {typeof window !== "undefined" && window.__NATIVE__ === true && (
          <AndroidUpdateSection />
        )}

        <SettingsSection
          title="法律与合规"
          description="用户协议与隐私政策。"
          divider
        >
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
            <Link
              to={APP_PATHS.more.legal.terms}
              className="text-foreground underline-offset-2 hover:underline"
            >
              用户协议
            </Link>
            <Link
              to={APP_PATHS.more.legal.privacy}
              className="text-foreground underline-offset-2 hover:underline"
            >
              隐私政策
            </Link>
          </div>
        </SettingsSection>
      </SettingsStack>
    </div>
  );
}
