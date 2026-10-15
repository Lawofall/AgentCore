/**
 * 桌面安装包地址（与官网 /download 同链：GitHub Releases）。
 *
 * 版本发现仍走品牌域 `latest.yml`；真正下的 ~190MB 安装包不走 Tunnel。
 * → 发布与门禁.md §1.6 / §1.6b
 */

export const DOWNLOADS_ORIGIN = "https://downloads.fashitianxia.xyz";
export const GITHUB_RELEASES_REPO = "Lawofall/AgentCore-releases";

export type DesktopReleaseChannel = "stable" | "beta";

export type LatestDesktopJson = {
  version?: string;
  winUrl?: string;
  macUrl?: string;
  winFilename?: string;
  macFilename?: string;
};

export function releaseChannelFromDefine(
  raw: string | undefined,
): DesktopReleaseChannel {
  return raw === "beta" ? "beta" : "stable";
}

export function desktopFeedBase(channel: DesktopReleaseChannel): string {
  return `${DOWNLOADS_ORIGIN}/desktop/${channel}`;
}

export function desktopLatestJsonUrl(channel: DesktopReleaseChannel): string {
  return `${desktopFeedBase(channel)}/latest.json`;
}

export function installerFilename(
  version: string,
  platform: string,
): string | null {
  const ver = version.trim();
  if (!ver) return null;
  if (platform === "darwin") return `GAgent-${ver}-mac-arm64.dmg`;
  if (platform === "win32") return `GAgent-${ver}-win-x64.exe`;
  return null;
}

export function githubInstallerUrl(version: string, filename: string): string {
  return `https://github.com/${GITHUB_RELEASES_REPO}/releases/download/v${version}/${filename}`;
}

function nonEmpty(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** 只取最后一段；`/` 与 `\\` 都当分隔符（清单可能来自任一侧）。 */
function installerBasename(value: string): string {
  const normalized = value.replace(/\\/g, "/");
  const slash = normalized.lastIndexOf("/");
  return slash >= 0 ? normalized.slice(slash + 1) : normalized;
}

function latestFilenameForPlatform(
  latest: LatestDesktopJson | null,
  platform: string,
): string | null {
  if (!latest) return null;
  if (platform === "win32") return nonEmpty(latest.winFilename);
  if (platform === "darwin") return nonEmpty(latest.macFilename);
  return null;
}

/**
 * 清单文件名只取 basename。含 `..`、仍含路径分隔符、空、或对不上该平台约定
 * 安装包名时回落 `installerFilename`。
 */
function safeInstallerFilename(raw: string | null, expected: string): string {
  if (!raw) return expected;
  if (raw.includes("..")) return expected;
  const base = installerBasename(raw).trim();
  if (!base) return expected;
  if (base.includes("/") || base.includes("\\")) return expected;
  if (base !== expected) return expected;
  return base;
}

/**
 * 解析本机该下的安装包。永远按 version + 安全文件名重算 GitHub 直链，忽略
 * `latest.json` 的 winUrl/macUrl（含旧品牌域、任意主机）。
 */
export function resolveInstallerArtifact(
  version: string,
  platform: string,
  latest: LatestDesktopJson | null,
): { url: string; filename: string } | null {
  const ver = version.trim();
  const fallbackName = installerFilename(ver, platform);
  if (!ver || !fallbackName) return null;

  const filename = safeInstallerFilename(
    latestFilenameForPlatform(latest, platform),
    fallbackName,
  );
  return { url: githubInstallerUrl(ver, filename), filename };
}

export function parseLatestDesktopJson(raw: unknown): LatestDesktopJson | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  return {
    version: nonEmpty(o.version) ?? undefined,
    winUrl: nonEmpty(o.winUrl) ?? undefined,
    macUrl: nonEmpty(o.macUrl) ?? undefined,
    winFilename: nonEmpty(o.winFilename) ?? undefined,
    macFilename: nonEmpty(o.macFilename) ?? undefined,
  };
}
