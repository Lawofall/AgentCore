import { dirname, resolve } from "path";
import { fileURLToPath } from "url";
import react from "@vitejs/plugin-react";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";
import { searchForWorkspaceRoot } from "vite";
import { viteClientBuildDefine } from "../../scripts/client-build-info.mjs";
import {
  parseReleaseChannel,
  resolveReleaseIdentity,
} from "./scripts/release-channel.mjs";
import { resolveApiBaseUrl } from "./scripts/resolve-api-base-url";

const clientBuildDefine = viteClientBuildDefine(
  new URL("./package.json", import.meta.url),
);
const releaseIdentity = resolveReleaseIdentity(
  parseReleaseChannel(process.env.DESKTOP_RELEASE_CHANNEL),
);
const channelBuildDefine = {
  __DESKTOP_RELEASE_CHANNEL__: JSON.stringify(releaseIdentity.channel),
  __WINDOWS_APP_USER_MODEL_ID__: JSON.stringify(
    releaseIdentity.windowsAppUserModelId,
  ),
};

// 包根（本文件所在目录）——与 renderer envDir / .env.production 同目录。
// 禁止用 process.cwd()：cwd ≠ 包根时主进程读不到 .env.production，CSP 会钉死 localhost，
// 而渲染层仍从包根 envDir 烤进生产 VITE_API_URL → 桌面「无法连接后端」。
const packageDir = dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode, command }) => {
  // 把渲染层构建期烘焙的后端地址（VITE_API_URL，见 .env.production）也喂给主进程，让主进程的 CSP
  // img-src / connect-src 能精确收窄到「自己 + 后端源」——既堵任意第三方远程图（渲染期信标 V2/V3：mermaid/markmap
  // 吐 <img src=evil> 在渲染期零点击取图），又放行后端头像 / favicon，并放行渲染层对生产 API 的 fetch。
  const apiBaseUrl = resolveApiBaseUrl(mode, command, packageDir);
  // 生产构建：把同一地址钉进 process.env，避免 Vite 渲染层 loadEnv 被壳里残留的 localhost 盖掉。
  if (command === "build" || mode === "production") {
    process.env.VITE_API_URL = apiBaseUrl;
  }

  return {
    main: {
      plugins: [externalizeDepsPlugin()],
      define: {
        __API_BASE_URL__: JSON.stringify(apiBaseUrl),
        ...channelBuildDefine,
      },
      resolve: {
        alias: {
          "@shared": resolve(packageDir, "src/shared"),
        },
      },
    },
    preload: {
      plugins: [externalizeDepsPlugin()],
      resolve: {
        alias: {
          "@shared": resolve(packageDir, "src/shared"),
        },
      },
    },
    renderer: {
      // electron-vite defaults root to src/renderer, so Vite's default publicDir would be
      // src/renderer/public. Point at package-root public/ instead.
      publicDir: resolve(packageDir, "public"),
      // 与主进程 resolveApiBaseUrl 同一包根，避免 cwd 漂移导致主/渲染 API 源分叉。
      envDir: packageDir,
      define: { ...clientBuildDefine, ...channelBuildDefine },
      // SECURITY (XSS-001 前端XSS·CSP): drop Vite's inline modulepreload polyfill. Electron's
      // bundled Chromium supports <link rel=modulepreload> natively, so the polyfill is dead
      // weight — and removing it means the built index.html has NO inline <script>, which is
      // what lets the app:// CSP use a strict `script-src 'self'` (see src/main/index.ts)
      // without a blank-screen regression.
      build: {
        modulePreload: { polyfill: false },
      },
      resolve: {
        alias: {
          "@": resolve(packageDir, "src/renderer"),
          "@shared": resolve(packageDir, "src/shared"),
          "@byok-presets": resolve(
            packageDir,
            "../server/agentcore/llm/byok_provider_presets.json",
          ),
        },
      },
      // Allow serving the monorepo root so the 前端预览 route (#/preview) can glob the
      // committed conformance vectors from packages/protocol-conformance/fixtures.
      server: {
        fs: { allow: [searchForWorkspaceRoot(packageDir)] },
      },
      // Force-prebundle mermaid: dynamic import("mermaid") otherwise races Vite's
      // deps optimizer and can resolve to the SPA HTML shell →「Failed to fetch
      // dynamically imported module …/mermaid.js」(see Diagram getMermaid).
      optimizeDeps: {
        include: ["mermaid"],
      },
      plugins: [react()],
    },
  };
});
