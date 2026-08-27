/**
 * 官方桌面客户端下载真源（与官网 / legal 同源）。
 * Web 无法履约本机目录授权 / 绑定 / 打开项目时引导此处，勿靠公网搜索冒充。
 */
export const DESKTOP_DOWNLOAD_URL =
  "https://fashitianxia.xyz/download" as const;

/** Ask 卡 / 错误文案：本机目录类 action 在无本地文件能力时的引导。 */
export const DESKTOP_REQUIRED_HINT = "请用桌面客户端" as const;

export const DESKTOP_REQUIRED_MESSAGE =
  `${DESKTOP_REQUIRED_HINT}完成授权或打开本机目录。下载：${DESKTOP_DOWNLOAD_URL}` as const;

/** 打开官方桌面下载页（浏览器新标签；Electron 下亦走 window.open → 系统浏览器）。 */
export function openDesktopDownloadPage(): void {
  if (typeof window === "undefined") return;
  window.open(DESKTOP_DOWNLOAD_URL, "_blank", "noopener,noreferrer");
}

/** AskOption.action：须桌面本地文件能力履约，Web 禁止退化成普通 choice 确认。 */
export function isDesktopFolderAction(
  action: string | undefined,
): action is
  | "open_local_project"
  | "register_local_project"
  | "bind_local_folder"
  | "grant_organize_folder" {
  return (
    action === "open_local_project" ||
    action === "register_local_project" ||
    action === "bind_local_folder" ||
    action === "grant_organize_folder"
  );
}

/** Web / 无本地文件：展示引导并打开下载页；不写入答案、不 toggleChoice。 */
export function guideDesktopDownload(): string {
  openDesktopDownloadPage();
  return DESKTOP_REQUIRED_MESSAGE;
}
