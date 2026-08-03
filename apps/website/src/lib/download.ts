/**
 * 桌面端 / Android 下载配置（构建时由 scripts/fetch-release.mjs 刷新）。
 * 用户面安装包 URL = GitHub Releases；版本发现可读品牌域 latest.json。
 */
import {
  ANDROID_APK_FILENAME,
  ANDROID_APK_URL,
  ANDROID_VERSION,
  DESKTOP_VERSION,
  MAC_DMG_FILENAME,
  MAC_DMG_URL,
  RELEASE_NOTES_URL,
  WIN_INSTALLER_FILENAME,
  WIN_INSTALLER_URL,
} from "./download.generated";

export {
  ANDROID_APK_FILENAME,
  ANDROID_APK_URL,
  ANDROID_VERSION,
  DESKTOP_VERSION,
  MAC_DMG_FILENAME,
  MAC_DMG_URL,
  RELEASE_NOTES_URL,
  WIN_INSTALLER_FILENAME,
  WIN_INSTALLER_URL,
};

/** Brand host for updater feed / latest.json（非官网首装主链）。 */
export const DOWNLOADS_BASE = "https://downloads.fashitianxia.xyz" as const;

export const RELEASES_REPO =
  "https://github.com/Lawofall/AgentCore-releases" as const;

/** 发布页 / 历史版本（官网安装包直链亦指向本仓 assets）。 */
export const RELEASES_LATEST = `${RELEASES_REPO}/releases/latest` as const;

export type PlatformId = "win" | "mac" | "linux" | "android";

/**
 * 平台的「事实」：是否已发布、直链、文件名。
 * 展示文案（平台名、系统要求、安装步骤）在 content/download.ts，勿在此处写死。
 */
export type PlatformDownload = {
  id: PlatformId;
  available: boolean;
  url?: string;
  fileLabel?: string;
};

/** Build platform rows from release artifact URLs (runtime or build-time). */
export function platformsFromArtifacts(artifacts: {
  winUrl: string;
  winFilename: string;
  macUrl: string;
  macFilename: string;
  androidUrl: string;
  androidFilename: string;
}): PlatformDownload[] {
  const macReady = Boolean(artifacts.macUrl);
  const androidReady = Boolean(artifacts.androidUrl);
  return [
    {
      id: "win",
      available: true,
      url: artifacts.winUrl,
      fileLabel: artifacts.winFilename,
    },
    {
      id: "mac",
      available: macReady,
      url: macReady ? artifacts.macUrl : undefined,
      fileLabel: macReady ? artifacts.macFilename : undefined,
    },
    {
      id: "android",
      available: androidReady,
      url: androidReady ? artifacts.androidUrl : undefined,
      fileLabel: androidReady ? artifacts.androidFilename : undefined,
    },
    { id: "linux", available: false },
  ];
}

export const PLATFORMS: PlatformDownload[] = platformsFromArtifacts({
  winUrl: WIN_INSTALLER_URL,
  winFilename: WIN_INSTALLER_FILENAME,
  macUrl: MAC_DMG_URL,
  macFilename: MAC_DMG_FILENAME,
  androidUrl: ANDROID_APK_URL,
  androidFilename: ANDROID_APK_FILENAME,
});

export const DOWNLOAD_PAGE_PATH = "/download" as const;

/** 手机端 web SPA（Cloudflare Pages · deploy-mobile-web.yml） */
export const MOBILE_WEB_URL = "https://m.fashitianxia.xyz" as const;

/** 主力 web 客户端（apps/desktop 渲染层跑浏览器，同源托管在 app. 根路径；免安装、需登录）。 */
export const WEB_APP_URL = "https://app.fashitianxia.xyz" as const;
