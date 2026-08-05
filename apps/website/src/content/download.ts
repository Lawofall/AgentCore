/**
 * 下载页文案单一来源（中英双语）。
 *
 * 这里只放「展示文案」；安装包 URL、文件名、版本号、是否已发布这些事实
 * 仍由 lib/download.ts 提供（构建时由 scripts/fetch-release.mjs 刷新）。
 * 两者刻意分开：文案可以随便改，事实不许在这里写死。
 */
import type { PlatformId } from "@/lib/download";
import type { T } from "@/content/home";

export const HERO = {
  eyebrow: { zh: "桌面客户端 · DESKTOP", en: "Desktop app" },
  title: { zh: "把整支团队，装进你的电脑", en: "Put the whole team on your desk" },
  lead: {
    zh: "本地文件、系统级工具、更长的会话。桌面客户端与网页版同源，只是能更深地接入你的机器。",
    en: "Local files, system-level tools, longer sessions. The desktop client is the same AgentCore — with deeper access to your machine.",
  },
  cta: { zh: "下载适用于本机的版本", en: "Download for your device" },
  detecting: { zh: "识别中…", en: "DETECTING…" },
};

/** 平台展示名与一句话说明。可用性与下载直链来自 lib/download.ts。 */
export const PLATFORM_COPY: Record<
  PlatformId,
  { label: string; subtitle: T; meta: T }
> = {
  win: {
    label: "Windows",
    subtitle: { zh: "Windows 10 及以上", en: "Windows 10 and later" },
    meta: {
      zh: "安装版。安装后可在设置 → 关于 检查更新，新版本会自动推送。",
      en: "Installer build. Check for updates in Settings → About; new versions are pushed automatically.",
    },
  },
  mac: {
    label: "macOS",
    subtitle: { zh: "macOS 13 Ventura 及以上", en: "macOS 13 Ventura and later" },
    meta: {
      zh: "仅 Apple Silicon（M 系列）。内测包未签名，首次打开须右键 →「打开」。",
      en: "Apple Silicon (M-series) only. The beta build is unsigned — right-click → Open the first time.",
    },
  },
  android: {
    label: "Android",
    subtitle: { zh: "Android 8.0 及以上", en: "Android 8.0 and later" },
    meta: {
      zh: "APK 直装。安装时需在系统设置中允许未知来源应用。",
      en: "Direct APK install. Allow unknown-source apps in system settings first.",
    },
  },
  linux: {
    label: "Linux",
    subtitle: { zh: "AppImage", en: "AppImage" },
    meta: { zh: "正在准备中。", en: "In the works." },
  },
};

export const PLATFORM_ORDER: PlatformId[] = ["win", "mac", "android", "linux"];

export const LABELS = {
  currentDevice: { zh: "当前设备", en: "YOUR DEVICE" },
  comingSoon: { zh: "即将推出", en: "COMING SOON" },
  notReleased: {
    zh: "本次构建尚未发布该平台安装包",
    en: "Not published in this build yet",
  },
  download: { zh: "下载", en: "Download" },
  allPlatforms: { zh: "全部平台 · ALL PLATFORMS", en: "All platforms" },
};

/** 免安装入口：与桌面端并列的两张快捷卡。 */
export const QUICK_ENTRIES = [
  {
    key: "web",
    title: { zh: "网页版 · 免安装", en: "Web app · no install" },
    body: {
      zh: "浏览器打开即用，功能与客户端一致",
      en: "Open it in the browser and start right away.",
    },
  },
  {
    key: "mobile",
    title: { zh: "手机网页版", en: "Mobile web" },
    body: {
      zh: "在手机上下达任务，回来再审阅产出",
      en: "Assign a task on the phone, review the result later.",
    },
  },
] as const;

/** 右栏系统要求：刻意做成紧凑的键值表，数值全部取自真实支持范围。 */
export const REQUIREMENTS = {
  eyebrow: { zh: "系统要求", en: "System requirements" },
  rows: [
    {
      k: { zh: "macOS", en: "macOS" },
      v: { zh: "13 Ventura+ · Apple Silicon", en: "13 Ventura+ · Apple Silicon" },
    },
    { k: { zh: "Windows", en: "Windows" }, v: { zh: "10 / 11 · 64 位", en: "10 / 11 · 64-bit" } },
    { k: { zh: "Android", en: "Android" }, v: { zh: "8.0 及以上", en: "8.0 and later" } },
    {
      k: { zh: "内存", en: "Memory" },
      v: { zh: "8 GB（推荐 16 GB）", en: "8 GB (16 GB recommended)" },
    },
    { k: { zh: "硬盘", en: "Disk" }, v: { zh: "约 500 MB 可用", en: "~500 MB free" } },
    {
      k: { zh: "网络", en: "Network" },
      v: { zh: "需联网 · 支持私有部署", en: "Required · self-hosting supported" },
    },
  ],
};

export const AUTO_UPDATE = {
  eyebrow: { zh: "自动更新", en: "Auto update" },
  body: {
    zh: "已安装用户无需重复下载——应用会在后台检查更新，就绪后提示重启安装。macOS 内测包更新后若无法启动，请再次右键 →「打开」。",
    en: "Installed users need not download again — the app checks in the background and prompts to restart when ready. If a macOS beta build won't launch after updating, right-click → Open once more.",
  },
};

export const RELEASE = {
  eyebrow: { zh: "发布说明 · RELEASE NOTES", en: "Release notes" },
  notes: { zh: "查看本版发布说明", en: "Read the release notes" },
  history: { zh: "所有历史版本（GitHub Releases）", en: "All past releases (GitHub)" },
};

/** 安装步骤：按平台分组，仅列已发布的平台。 */
export const INSTALL_STEPS: Partial<Record<PlatformId, T[]>> = {
  win: [
    {
      zh: "下载并运行安装程序，按向导完成安装。",
      en: "Download and run the installer, then follow the wizard.",
    },
    { zh: "首次启动注册账号并登录。", en: "Register and sign in on first launch." },
    {
      zh: "在设置 → 关于 可检查更新；已安装用户会自动收到新版本。",
      en: "Check for updates in Settings → About; installed users get new versions automatically.",
    },
  ],
  mac: [
    {
      zh: "下载 DMG，将 AgentCore 拖入「应用程序」文件夹。",
      en: "Download the DMG and drag AgentCore into Applications.",
    },
    {
      zh: "首次打开：右键 AgentCore →「打开」→ 确认（内测包未签名，勿直接双击）。",
      en: "First launch: right-click AgentCore → Open → confirm. The beta build is unsigned, so don't double-click.",
    },
    {
      zh: "注册账号并登录；设置 → 关于 可检查更新。",
      en: "Register and sign in; check for updates in Settings → About.",
    },
  ],
  android: [
    {
      zh: "下载 APK 后，在系统设置中允许安装未知来源应用。",
      en: "After downloading the APK, allow unknown-source installs in system settings.",
    },
    {
      zh: "打开文件并安装，首次启动注册账号并登录。",
      en: "Open the file to install, then register and sign in on first launch.",
    },
  ],
};

export const INSTALL = {
  eyebrow: { zh: "安装步骤 · INSTALL", en: "Install" },
};
