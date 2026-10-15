/**
 * Brand download CDN + GitHub Releases URL helpers.
 *
 * - 官网首装 / 用户面安装包按钮 → GitHub Releases（`releases/download/...`）
 * - electron-updater feed + latest.json 宿主 → 品牌域 downloads.*（自有机 nginx）
 * - GitHub AgentCore-releases 同时是上传源与历史归档
 *
 * Desktop layout on brand host（§7.6c 双轨）:
 *   {BASE}/desktop/stable/latest.yml|latest-mac.yml|latest.json|AgentCore-*
 *   {BASE}/desktop/beta/…（同上；仅测试通道）
 *   {BASE}/desktop/latest.yml|…  — 旧客户端兼容：sync stable 时镜像与 stable 同内容；
 *     beta 绝不写入扁平 desktop/ 或 stable/
 *   {BASE}/android/latest.json|AgentCore-*-android.apk
 *
 * → docs/05-平台与运维/发布与门禁.md §1.6b / §1.6c
 */

/** @typedef {"stable" | "beta"} DesktopChannel */

/** Resolve base URL — Node (sync/fetch-release) may override via env; Pages Functions have no `process`. */
function resolveDownloadsBase() {
  try {
    const fromEnv =
      typeof process !== "undefined" &&
      process.env &&
      typeof process.env.AGENTCORE_DOWNLOADS_BASE === "string"
        ? process.env.AGENTCORE_DOWNLOADS_BASE.trim()
        : "";
    if (fromEnv) return fromEnv;
  } catch {
    // ignore
  }
  return "https://downloads.fashitianxia.xyz";
}

export const DOWNLOADS_BASE = resolveDownloadsBase();

export const DOWNLOADS_DESKTOP_PREFIX = "desktop";
export const DOWNLOADS_ANDROID_PREFIX = "android";

/** @type {readonly DesktopChannel[]} */
export const DESKTOP_CHANNELS = Object.freeze(["stable", "beta"]);
export const DESKTOP_CHANNEL_DEFAULT = /** @type {DesktopChannel} */ ("stable");

export const RELEASES_REPO = "Lawofall/AgentCore-releases";
export const RELEASES_REPO_URL = `https://github.com/${RELEASES_REPO}`;

/**
 * @param {string} value
 * @returns {DesktopChannel}
 */
export function normalizeDesktopChannel(value) {
  const c = String(value ?? "")
    .trim()
    .toLowerCase();
  if (c === "beta") return "beta";
  if (c === "stable" || c === "") return "stable";
  throw new Error(`Invalid desktop channel: ${value} (use stable|beta)`);
}

/**
 * Channel directory under desktop/ (e.g. "desktop/stable").
 * @param {DesktopChannel} [channel]
 */
export function desktopChannelPrefix(channel = DESKTOP_CHANNEL_DEFAULT) {
  return `${DOWNLOADS_DESKTOP_PREFIX}/${normalizeDesktopChannel(channel)}`;
}

/**
 * Flat legacy prefix for old installed clients (`desktop/latest.yml` feed).
 * Only stable sync may write here (mirror of desktop/stable/).
 */
export function desktopLegacyFlatPrefix() {
  return DOWNLOADS_DESKTOP_PREFIX;
}

/**
 * Remote relative dirs to write for a channel sync.
 * stable → ["desktop/stable", "desktop"]；beta → ["desktop/beta"] only.
 * @param {DesktopChannel} [channel]
 * @returns {string[]}
 */
export function desktopSyncDestPrefixes(channel = DESKTOP_CHANNEL_DEFAULT) {
  const ch = normalizeDesktopChannel(channel);
  if (ch === "beta") return [desktopChannelPrefix("beta")];
  return [desktopChannelPrefix("stable"), desktopLegacyFlatPrefix()];
}

/** @param {string} version */
export function winInstallerFilename(version) {
  return `GAgent-${version}-win-x64.exe`;
}

/** @param {string} version */
export function macDmgFilename(version) {
  return `GAgent-${version}-mac-arm64.dmg`;
}

/** @param {string} version */
export function androidApkFilename(version) {
  return `GAgent-${version}-android.apk`;
}

/** @param {string} version */
export function githubReleaseNotesUrl(version) {
  return `${RELEASES_REPO_URL}/releases/tag/v${version}`;
}

/** @param {string} version */
export function githubAndroidReleaseNotesUrl(version) {
  return `${RELEASES_REPO_URL}/releases/tag/android-v${version}`;
}

/**
 * User-facing desktop installer URL (GitHub Releases asset).
 * @param {string} version
 * @param {string} filename
 */
export function githubDesktopAssetUrl(version, filename) {
  return `${RELEASES_REPO_URL}/releases/download/v${version}/${filename}`;
}

/**
 * User-facing Android APK URL (GitHub Releases asset).
 * @param {string} version
 * @param {string} filename
 */
export function githubAndroidAssetUrl(version, filename) {
  return `${RELEASES_REPO_URL}/releases/download/android-v${version}/${filename}`;
}

/**
 * Absolute brand-host URL for a key (updater feed / manifests — not官网首装主链).
 * @param {string} key e.g. "desktop/stable/latest.yml"
 */
export function cdnUrl(key) {
  const base = DOWNLOADS_BASE.replace(/\/$/, "");
  const path = String(key).replace(/^\//, "");
  return `${base}/${path}`;
}

/**
 * electron-updater generic feed directory for a channel.
 * @param {DesktopChannel} [channel]
 */
export function desktopFeedUrl(channel = DESKTOP_CHANNEL_DEFAULT) {
  return cdnUrl(desktopChannelPrefix(channel));
}

/**
 * @param {DesktopChannel} [channel]
 */
export function desktopLatestJsonUrl(channel = DESKTOP_CHANNEL_DEFAULT) {
  return cdnUrl(`${desktopChannelPrefix(channel)}/latest.json`);
}

export function androidLatestJsonUrl() {
  return cdnUrl(`${DOWNLOADS_ANDROID_PREFIX}/latest.json`);
}

/**
 * Build website artifact URLs for a known desktop version (GitHub Releases).
 * Filenames may be overridden from CDN latest.json (always reconstruct GitHub URLs).
 *
 * @param {string} version
 * @param {{ macFilename?: string, winFilename?: string }} [opts]
 */
export function artifactUrlsForVersion(version, opts = {}) {
  const winFilename =
    opts.winFilename === undefined
      ? winInstallerFilename(version)
      : opts.winFilename;
  const macFilename =
    opts.macFilename === undefined
      ? macDmgFilename(version)
      : opts.macFilename;
  return {
    version,
    releaseNotesUrl: githubReleaseNotesUrl(version),
    winUrl: winFilename
      ? githubDesktopAssetUrl(version, winFilename)
      : "",
    winFilename: winFilename || "",
    macUrl: macFilename
      ? githubDesktopAssetUrl(version, macFilename)
      : "",
    macFilename: macFilename || "",
  };
}

/**
 * @param {string} version
 * @param {string} [filename]
 */
export function androidArtifactUrls(version, filename) {
  const apkName = filename || androidApkFilename(version);
  return {
    androidVersion: version,
    androidFilename: apkName,
    androidUrl: githubAndroidAssetUrl(version, apkName),
  };
}

/**
 * Desktop feed manifest written on sync (website discovers version here;
 * winUrl/macUrl are GitHub so官网不依赖品牌域带宽).
 * @param {{
 *   version: string,
 *   winFilename: string,
 *   macFilename?: string,
 *   releaseNotesUrl?: string,
 * }} input
 */
export function buildDesktopLatestJson(input) {
  const macFilename = input.macFilename || "";
  return {
    version: input.version,
    releaseNotesUrl:
      input.releaseNotesUrl || githubReleaseNotesUrl(input.version),
    winFilename: input.winFilename,
    macFilename,
    winUrl: githubDesktopAssetUrl(input.version, input.winFilename),
    macUrl: macFilename
      ? githubDesktopAssetUrl(input.version, macFilename)
      : "",
    updatedAt: new Date().toISOString(),
  };
}

/**
 * @param {{ version: string, filename?: string }} input
 */
export function buildAndroidLatestJson(input) {
  const filename = input.filename || androidApkFilename(input.version);
  const urls = androidArtifactUrls(input.version, filename);
  return {
    version: input.version,
    filename,
    downloadUrl: urls.androidUrl,
    releaseNotesUrl: githubAndroidReleaseNotesUrl(input.version),
    updatedAt: new Date().toISOString(),
  };
}
