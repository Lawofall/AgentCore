/**
 * AgentCore 桌面端打包配置（P2-1；docs/05-平台与运维/发布与门禁.md §1.6、
 * docs/04-前端/前端技术与架构.md §7.6；通道定案 §1.6c）。
 *
 * 出 Windows(NSIS) / macOS(DMG+zip, arm64) / Linux(AppImage)；
 * MVP 不签名（内测；正式发布前再申请代码签名 / Mac 公证）。Win 优先。
 *
 * 用法：
 *   pnpm build:unpack   # 仅解包到 release/<ver>/，快速验证打包（不编译 NSIS）
 *   pnpm build:win      # 出 Windows NSIS 安装包
 *   DESKTOP_RELEASE_CHANNEL=beta pnpm build:win  # 测试轨并列身份 + feed
 * 产物落 release/<version>/（被 .gitignore 忽略）。
 *
 * 通道由 `scripts/release-channel.mjs` 解析；默认 stable。
 * publish 块只决定打进包内的 app-update.yml feed URL —— 不走 electron-builder 上传
 *（Win/Mac 均为 --publish never + gh upload）。
 */
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  parseReleaseChannel,
  resolveReleaseIdentity,
} from "./scripts/release-channel.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const identity = resolveReleaseIdentity(
  parseReleaseChannel(process.env.DESKTOP_RELEASE_CHANNEL),
);

/** @param {string} rel */
function abs(rel) {
  return join(root, rel);
}

/** @type {import('electron-builder').Configuration} */
const config = {
  appId: identity.appId,
  productName: identity.productName,

  // 应用图标按平台分源（母版 assets/agentcore-icon.svg，导出 PNG 入仓）：
  // - Win / Linux：icon-win.png = rounded（四角透明 squircle）。Windows 快捷方式/任务栏不会再套一层
  //   圆角，若用满铺直角源会呈「方角块」；须预先烤好透明角。
  // - macOS：icon-mac.png = cropped（满铺、四角不透明）。系统会自己做 mask，预圆角反而被裁切/发糊。
  // 测试轨：resources/channel-icons/*-beta.png（「测」角标，见 scripts/generate-beta-icons.mjs）。
  // 运行时窗口/任务栏图标另在 resources/icon.png（= rounded；经 ?asset，见 main/index.ts）。
  directories: {
    output: "release/${version}",
    buildResources: "build",
  },

  // out/ 是 electron-vite 自包含产物（renderer 已 bundle；main/preload 仅保留少量运行时
  // 外部依赖）。renderer 专用包放在 package.json devDependencies，electron-builder 不会
  // 把它们打进 node_modules。shoot 截图在 shoot-out/（须在 out/ 外）；下列排除为
  // 历史/防御性规则，防止旧路径或手误产物再次进包。
  files: [
    "out/**",
    "package.json",
    "!out/preview/**",
    "!out/preview-debate/**",
    "!out/cmdregion/**",
    "!out/route-shots/**",
  ],

  // node-pty 原生 .node / conpty DLL 不可进 asar（运行时 dlopen 失败）；解包到 app.asar.unpacked。
  asarUnpack: ["**/node_modules/node-pty/**"],

  // node-pty 官方 prebuilds 走 N-API，Electron ABI 可直接加载；打包时勿强制 node-gyp
  // rebuild（本机缺 Spectre 缓解库时会失败）。asarUnpack 见上。
  npmRebuild: false,
  nodeGypRebuild: false,

  // 内置 Python 运行时（双模式工作区 §十「内置 Python 打包」方案 B）：由 scripts/bundle-sidecar.mjs
  // 预构建到 resources/sidecar/（独立 CPython + --target site-packages），随包拷入应用
  // resources/sidecar/。主进程 resolveSpawnConfig 在 app.isPackaged 时据此拉起 sidecar，
  // 用户机器无需任何系统 Python / venv / uv。须在 electron-builder 前跑 `pnpm bundle:sidecar`
  // （package.json 的 build:* 已前置）；原生 wheel/解释器不可交叉编译，故每平台各自 CI 构建。
  // 内嵌 ripgrep（产品 AI grep）：scripts/fetch_ripgrep.py --install-desktop → resources/rg/；
  // 主进程 opGrep 与 sidecar（AGENTCORE_RG_PATH）共用，不依赖 PATH。
  extraResources: [
    { from: "resources/sidecar", to: "sidecar" },
    {
      from: "resources/rg",
      to: "rg",
      filter: ["**/*"],
    },
  ],

  // Artifact slug is ASCII `AgentCore` for both channels（CDN/官网文件名约定单一）；
  // channel identity = appId / productName / publish.url / icons.
  artifactName: `${identity.artifactSlug}-\${version}-\${os}-\${arch}.\${ext}`,

  win: {
    icon: abs(identity.winIcon),
    target: ["nsis"],
  },

  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: identity.shortcutName,
  },

  mac: {
    icon: abs(identity.macIcon),
    // dmg = 首装与当前「下载安装包」路径；zip / latest-mac.yml 仍上传供版本检查。
    // 内测未签名：Gatekeeper 常报「已损坏」，首装/替换后须 xattr -cr；Squirrel.Mac 拒装未签名
    // 更新，主进程 codesign 探测到无签名身份即降级为引导手动下载（发布与门禁.md §1.6）。
    target: ["dmg", "zip"],
    category: "public.app-category.productivity",
  },

  linux: {
    icon: abs(identity.linuxIcon),
    target: ["AppImage"],
    category: "Utility",
  },

  // electron-updater 用户面 feed = 品牌下载域按通道分目录（…/desktop/stable|beta）。
  // 官网首装按钮 = GitHub Releases；安装包仍先 `gh release upload` 到 AgentCore-releases，再 sync:release-cdn。
  publish: {
    provider: "generic",
    url: identity.publishUrl,
  },
};

export default config;
