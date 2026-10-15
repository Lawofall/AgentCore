/**
 * Desktop release channel → install identity + updater feed (定案 §7.6c).
 *
 * Channel is build-time only (`DESKTOP_RELEASE_CHANNEL` / `--channel=`).
 * Defaults to `stable`. Updater still reads builder-written app-update.yml
 * (no setFeedURL).
 */

/** @typedef {"stable" | "beta"} DesktopReleaseChannel */

export const DESKTOP_DOWNLOAD_ORIGIN = "https://fashitianxia.xyz/download";

export const RELEASE_CHANNEL_ENV = "DESKTOP_RELEASE_CHANNEL";

/**
 * @param {string | undefined | null} raw
 * @returns {DesktopReleaseChannel}
 */
export function parseReleaseChannel(raw) {
  const v = String(raw ?? "stable")
    .trim()
    .toLowerCase();
  if (v === "" || v === "stable") return "stable";
  if (v === "beta") return "beta";
  throw new Error(
    `Invalid ${RELEASE_CHANNEL_ENV}="${raw}" (expected stable|beta)`,
  );
}

/**
 * Prefer `--channel=beta` / `--channel beta`, else env, else stable.
 * @param {string[]} [argv]
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {DesktopReleaseChannel}
 */
export function resolveChannelFromArgv(
  argv = process.argv.slice(2),
  env = process.env,
) {
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--channel=")) {
      return parseReleaseChannel(a.slice("--channel=".length));
    }
    if (a === "--channel") {
      return parseReleaseChannel(argv[i + 1]);
    }
  }
  return parseReleaseChannel(env[RELEASE_CHANNEL_ENV]);
}

/**
 * @param {DesktopReleaseChannel} channel
 * @returns {DesktopReleaseChannel}
 */
export function otherReleaseChannel(channel) {
  return channel === "beta" ? "stable" : "beta";
}

/**
 * Official download page with `?channel=` (aligns with website block).
 * @param {DesktopReleaseChannel} channel
 */
export function desktopDownloadUrlForChannel(channel) {
  const ch = parseReleaseChannel(channel);
  return `${DESKTOP_DOWNLOAD_ORIGIN}?channel=${ch}`;
}

/**
 * @param {DesktopReleaseChannel} [channel]
 */
export function resolveReleaseIdentity(channel) {
  const ch = parseReleaseChannel(channel ?? "stable");
  if (ch === "beta") {
    const appId = "xyz.fashitianxia.gagent.beta";
    return {
      channel: /** @type {const} */ ("beta"),
      appId,
      productName: "GAgent 测试版",
      shortcutName: "GAgent 测试版",
      publishUrl: "https://downloads.fashitianxia.xyz/desktop/beta",
      // Same ASCII slug as stable so CDN/sync/官网文件名约定（GAgent-${ver}-…）不断缝；
      // 通道靠 appId / productName / feed 目录 / GitHub tag 预发布后缀区分。
      artifactSlug: "GAgent",
      channelLabelZh: "测试",
      // 必须等于 appId（NSIS 快捷方式 AUMID）。与稳定轨不同，避免两套安装并进任务栏。
      windowsAppUserModelId: appId,
      winIcon: "resources/channel-icons/icon-win-beta.png",
      macIcon: "resources/channel-icons/icon-mac-beta.png",
      linuxIcon: "resources/channel-icons/icon-win-beta.png",
      runtimeIcon: "resources/icon-beta.png",
    };
  }
  const appId = "xyz.fashitianxia.gagent";
  return {
    channel: /** @type {const} */ ("stable"),
    appId,
    productName: "GAgent",
    shortcutName: "GAgent",
    publishUrl: "https://downloads.fashitianxia.xyz/desktop/stable",
    artifactSlug: "GAgent",
    channelLabelZh: "稳定",
    // 必须等于 appId。分叉则 Win 系统通知静默失败。
    windowsAppUserModelId: appId,
    winIcon: "build/icon-win.png",
    macIcon: "build/icon-mac.png",
    linuxIcon: "build/icon-win.png",
    runtimeIcon: "resources/icon.png",
  };
}
