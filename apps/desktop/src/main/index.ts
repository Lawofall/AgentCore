import { join, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { is } from "@electron-toolkit/utils";
import { isSafeExternalUrl } from "@shared/safe-url";
import { WINDOW_CHANNELS } from "@shared/window-contract";
import {
  net,
  BrowserWindow,
  app,
  ipcMain,
  nativeTheme,
  protocol,
  shell,
} from "electron";
import iconBeta from "../../resources/icon-beta.png?asset";
// `?asset` 让 electron-vite 把图标拷入产物并解析为运行时绝对路径；用作窗口/任务栏图标
// （resources/icon.png = rounded squircle；测试轨用 icon-beta.png。打包 exe/.app 图标另由
// electron-builder 按平台分源：build/icon-win|mac.png 或 resources/channel-icons/*-beta.png）。
import iconStable from "../../resources/icon.png?asset";
import {
  apiOriginForCsp,
  connectSrcForCsp,
  decodeAppRelativePath,
  frameSrcForCsp,
} from "./app-protocol-csp";
import { installAuthCookieFlushOnQuit } from "./auth-client";
import { registerBrowserIpc, startDesktopBrowserBridge } from "./browser";
import { WORKSPACE_SCHEME } from "./browser/workspace-paths";
import { registerDeviceIdentityIpc } from "./device-identity";
import {
  buildFloatHashRoute,
  destroyAllFloatWindows,
  minimizeBrowserWindow,
  registerFloatWindowIpc,
} from "./float-window";
import { registerFsIpc, sweepOpenTempOrphans } from "./fs-service";
import { registerHostIpc } from "./host-service";
import {
  registerLocalStoreIpc,
  sweepOrphanLocalStoreFiles,
} from "./local-store";
import { registerLogIpc } from "./log-service";
import { registerMcpIpc, shutdownAllMcpSessions } from "./mcp-service";
import { registerNotificationIpc } from "./notification-service";
import { registerOutboxIpc } from "./outbox-writeback";
// 主进程安全网须最先加载：拦截 updater/net 层未捕获的网络瞬态，避免 Electron 默认错误框。
// （模块加载时已自注册；此处再调一次幂等，保证入口显式依赖。）
import { installProcessSafetyNet } from "./process-safety-net";
import { registerProcessIpc } from "./process-service";
import { registerPtyIpc } from "./pty-service";
import { registerSidecarIpc } from "./sidecar-service";
import { registerTerminalIpc } from "./terminal-service";
import { initUpdater } from "./updater";
import { registerWindowFrameIpc } from "./window-frame";
import { loadWindowState, manageWindowState } from "./window-state";

/** 主 BrowserWindow（真窗 closed 事件目标；窗控按 sender 路由后不再闭包绑死它）。 */
let mainWindowRef: BrowserWindow | null = null;
let windowChromeIpcRegistered = false;

function preloadPath(): string {
  return join(__dirname, "../preload/index.js");
}

function rendererLoadBase(): string {
  if (is.dev && process.env.ELECTRON_RENDERER_URL) {
    return process.env.ELECTRON_RENDERER_URL;
  }
  return `${APP_ORIGIN}/index.html`;
}

function allowedNavigationBase(): string {
  if (is.dev && process.env.ELECTRON_RENDERER_URL) {
    return process.env.ELECTRON_RENDERER_URL;
  }
  return APP_ORIGIN;
}

/** 窗控按 webContents→窗 路由，主窗与真 OS 浮窗共用同一 preload API。 */
function registerWindowChromeIpc(): void {
  if (windowChromeIpcRegistered) return;
  windowChromeIpcRegistered = true;
  ipcMain.on(WINDOW_CHANNELS.minimize, (e) => {
    const win = BrowserWindow.fromWebContents(e.sender);
    if (win) minimizeBrowserWindow(win);
  });
  ipcMain.on(WINDOW_CHANNELS.maximize, (e) => {
    const win = BrowserWindow.fromWebContents(e.sender);
    if (!win) return;
    win.isMaximized() ? win.unmaximize() : win.maximize();
  });
  ipcMain.on(WINDOW_CHANNELS.close, (e) => {
    BrowserWindow.fromWebContents(e.sender)?.close();
  });
  ipcMain.on(WINDOW_CHANNELS.setThemeSource, (_e, theme: unknown) => {
    if (theme === "light" || theme === "dark" || theme === "system") {
      nativeTheme.themeSource = theme;
    }
  });
}

installProcessSafetyNet();

// Overlay scrollbars must be enabled before `ready`. They auto-hide per pane and
// paint over content (Win11 / macOS). Fluent overlay is folded into this flag.
// The renderer must not set `::-webkit-scrollbar { width }` — that forces
// Blink's classic gutter bars.
app.commandLine.appendSwitch("enable-features", "OverlayScrollbar");

// Production renderer is served from a custom app:// scheme instead of file://,
// so it gets a real, stable origin (app://agentcore). That origin is what makes
// credentialed cross-origin calls to the cloud API governable by CORS + cookies
// (前端技术与架构.md §7.2) — a file:// (null/opaque) origin can't be allowlisted.
// Scheme privileges must be registered before the app `ready` event.
const APP_SCHEME = "app";
const APP_ORIGIN_HOST = "agentcore"; // renderer origin = app://agentcore
const APP_ORIGIN = `${APP_SCHEME}://${APP_ORIGIN_HOST}`;
const RENDERER_ROOT = join(__dirname, "../renderer");

// SECURITY (XSS-001 前端XSS·纵深 CSP): the packaged renderer is served over app://, so we
// stamp a Content-Security-Policy on every app:// response — the containment layer for any
// future DOM-XSS.
//
// 设计取舍（最正确设计，非便利妥协）: `script-src 'self'` WITHOUT `'unsafe-eval'` /
// `'unsafe-inline'`. mermaid 的图表源是【攻击者可影响】的（模型 / 间接注入可吐 ```mermaid
// 块），而 `'unsafe-eval'` 会把 eval/new Function 在【整个文档】放开——正好是恶意 mermaid 块
// 把「解析图表」变成「主源代码执行」所需的原语，所以绝不全局放开 eval。
// 实测（apps/mobile 打包产物，同一 mermaid 包）证明严格策略可行：mermaid v11 把每种图表当成普通
// 动态 import() 的 ES chunk 从 'self' 加载（script-src 'self' 已覆盖），全程无 new Worker /
// createObjectURL / 真 eval；唯一的 Function 构造器用法是 lodash 取全局的 `Function("return this")()`，
// 在浏览器里被前面的 `self` 短路、根本不执行。`script-src 'self'` 可行的另一前提是 built index.html
// 无 inline `<script>`（electron.vite.config.ts 关掉 Vite 的 modulepreload polyfill）。
// `style-src` 必须留 'unsafe-inline'——React / Tailwind / KaTeX 用 style【属性】，CSP 的 nonce/hash
// 管不到 style 属性，且样式注入风险远低于脚本。
// NOTE: 此 header 仅作用于 app://（prod）；`pnpm dev` 经 loadURL 走 Vite server，HMR 不受影响。
// 兜底阶梯（若未来 mermaid 改为主线程 eval 而报错）: 升级为 mermaid securityLevel:'sandbox'
// （沙箱 iframe 隔离其动态代码），而【绝不】全局加 'unsafe-eval'。

// 后端源：由 electron.vite.config.ts 的 main.define 在构建期注入（= 渲染层 VITE_API_URL 的同源，
// 见 .env.production）。用于把 img-src 精确收窄到「自己 + 后端」——只放行后端头像 / favicon，任意
// 第三方远程图被 Chromium 拦死。无法解析（极端构建配置缺失）→ 空串 → 退化为「只允许自己 + data:」。
declare const __API_BASE_URL__: string;
declare const __DESKTOP_RELEASE_CHANNEL__: string | undefined;
declare const __WINDOWS_APP_USER_MODEL_ID__: string | undefined;
const API_ORIGIN = apiOriginForCsp(__API_BASE_URL__);
const WINDOWS_APP_USER_MODEL_ID =
  typeof __WINDOWS_APP_USER_MODEL_ID__ !== "undefined" &&
  __WINDOWS_APP_USER_MODEL_ID__
    ? __WINDOWS_APP_USER_MODEL_ID__
    : "com.agentcore.desktop";
const icon =
  typeof __DESKTOP_RELEASE_CHANNEL__ !== "undefined" &&
  __DESKTOP_RELEASE_CHANNEL__ === "beta"
    ? iconBeta
    : iconStable;

// connect-src（XSS-001·纵深）: connect-src 管的是渲染层 fetch / SSE / WebSocket 出网。后端源是
// 【构建期】烘焙的——渲染层 services/api.ts 的 BASE_URL 与本 CSP 的 __API_BASE_URL__ 同出一个
// VITE_API_URL（见 electron.vite.config.ts），故全应用只有一个后端源、可钉死它。渲染层每个请求都走
// `${BASE_URL}/...`（REST + fetch 式 SSE；今日无 WebSocket、无跨源 fetch），所以收窄到「自己 + 该源」
// 对真实流量是 no-op，却把 connect-src 变成 script-src 'self' 背后的【外泄墙】（未来即便出 DOM-XSS 也
// 无处 POST 数据 / 开 socket）。源不可解析（极端构建缺 env）时【失败收紧】到 `'self'`——与 img-src
// 失败退化对称，禁止放开 https:/http:/ws:/wss:。ws/wss 收同源（后端 http→ws / https→wss）。
if (!API_ORIGIN) {
  console.warn(
    "[SECURITY][CSP] __API_BASE_URL__ unparseable; connect-src/img-src fail-closed " +
      "to 'self' (no remote https:/http:/ws:/wss:). Fix the build-time API URL.",
  );
}
const CONNECT_SRC = connectSrcForCsp(API_ORIGIN);
const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  // worker-src = 前瞻防御：当前 mermaid 不开 worker（走 dynamic import chunk），但若未来版本把
  // 解析挪进 Web Worker，self + blob 让动态能力留在 worker 边界内，仍不必污染主文档 script-src。
  "worker-src 'self' blob:",
  "style-src 'self' 'unsafe-inline'",
  // 渲染期外泄信标·纵深防线（红队 2026-06-30 · V1/V2/V3 同一类）：vega/mermaid/markmap 等引擎会在
  // 渲染期对 <img src=远程> / data.url 零点击取资源（DOMPurify 只清脚本/事件、不挡「取图」这种联网，
  // 故 mermaid strict 也挡不住）。img-src 只放行 自己 + data: 内联 + 你的后端源（头像/favicon），任意
  // 第三方远程图被浏览器拦在网络层——比逐引擎加门卫更治本（连未来新增的图表引擎一并覆盖）。
  // blob:：IM / 工作区等「cookie 鉴权 fetch → createObjectURL → <img>」路径所需（与 preview/paths.ts
  // 的 PREVIEW_CSP 对齐）。blob: 只展示本页已鉴权拿到的字节，不引入新的网络取图面，故不削弱
  // 「拦第三方远程图」红队目标。
  `img-src 'self' data: blob:${API_ORIGIN ? ` ${API_ORIGIN}` : ""}`,
  "font-src 'self' data:",
  CONNECT_SRC,
  // PDF 面板预览：iframe + blob:/data:（见 FilePreviewBody）；不含 https:，勿与 workspace CSP 混用。
  frameSrcForCsp(),
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
].join("; ");

protocol.registerSchemesAsPrivileged([
  {
    scheme: APP_SCHEME,
    privileges: {
      standard: true, // proper origin semantics (app://host/path)
      secure: true, // secure context → allows Secure cookies, etc.
      supportFetchAPI: true, // renderer can use fetch (API client + SSE)
      corsEnabled: true, // cross-origin requests go through CORS
    },
  },
  {
    // Local Browser 工作区 HTML（workspace://{folder|conv}.{id}/{path}；
    // partition 按 conversationId 切开，处理器 per-partition 注册）。
    // standard=true 才有层级 URL 语义 → 相对路径引用能按文档 URL 正确解析；
    // secure=true 给隔离页安全上下文；stream 支持大文件流式代理。
    scheme: WORKSPACE_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
      stream: true,
    },
  },
]);

// Serve the built renderer bundle over app://agentcore/<path>. HashRouter keeps
// every route on index.html (only the hash changes), so no SPA path fallback is
// needed. Reads are confined to RENDERER_ROOT (path-traversal guard).
function registerAppProtocol(): void {
  protocol.handle(APP_SCHEME, async (request) => {
    const { pathname } = new URL(request.url);
    const relativePath = decodeAppRelativePath(pathname);
    if (relativePath === null) {
      return new Response("Bad Request", { status: 400 });
    }
    const filePath = join(RENDERER_ROOT, relativePath);
    if (!filePath.startsWith(RENDERER_ROOT + sep)) {
      return new Response("Forbidden", { status: 403 });
    }
    const res = await net.fetch(pathToFileURL(filePath).toString());
    // Stamp the CSP on every app:// response (it only takes effect on the HTML document;
    // harmless on assets) so the renderer always loads under the policy.
    const headers = new Headers(res.headers);
    headers.set("Content-Security-Policy", CONTENT_SECURITY_POLICY);
    return new Response(res.body, {
      status: res.status,
      statusText: res.statusText,
      headers,
    });
  });
}

function createWindow(): BrowserWindow {
  // 恢复上次的窗口尺寸/位置/最大化（x/y 缺省时由 OS 居中）。
  const windowState = loadWindowState();
  const mainWindow = new BrowserWindow({
    width: windowState.width,
    height: windowState.height,
    x: windowState.x,
    y: windowState.y,
    title: is.dev ? "AgentCore [DEV]" : "AgentCore",
    minWidth: 800,
    minHeight: 600,
    show: false,
    frame: false,
    icon,
    ...(process.platform === "darwin" && {
      titleBarStyle: "hidden",
      trafficLightPosition: { x: 12, y: 12 },
    }),
    autoHideMenuBar: true,
    webPreferences: {
      preload: preloadPath(),
      // SECURITY (XSS-003 前端XSS·渲染进程沙箱): run the renderer in the OS sandbox. The
      // preload is sandbox-compatible — it only uses contextBridge + ipcRenderer (no Node
      // built-ins / npm Node deps), so the contextBridge API surface is unchanged. With
      // contextIsolation (default-on) + nodeIntegration (default-off), this shrinks the
      // blast radius of any renderer compromise to a sandboxed process.
      sandbox: true,
    },
  });
  mainWindowRef = mainWindow;
  mainWindow.on("closed", () => {
    if (mainWindowRef === mainWindow) mainWindowRef = null;
    // 关主窗 ≈ 收应用：收尽真窗，避免只剩浮窗挂起进程。
    destroyAllFloatWindows();
  });
  if (windowState.isMaximized) mainWindow.maximize();
  manageWindowState(mainWindow);
  registerWindowFrameIpc(mainWindow);
  registerWindowChromeIpc();

  // Dev-only: forward the renderer's console warnings/errors to this process's
  // stdout so a renderer crash (e.g. a React error-boundary stack logged via
  // console.error) shows up in the `pnpm dev` terminal, not only in DevTools.
  // Electron 35+ passes details on the event object; level is a string.
  if (is.dev) {
    mainWindow.webContents.on(
      "console-message",
      ({ level, message, lineNumber, sourceId }) => {
        if (level !== "warning" && level !== "error") return;
        const tag = level === "error" ? "renderer:error" : "renderer:warn";
        // 父终端/管道已断时 console.log 会同步抛 EPIPE——吞掉，别弹主进程错误框。
        try {
          console.log(`[${tag}] ${message} (${sourceId}:${lineNumber})`);
        } catch {
          /* ignore */
        }
      },
    );
  }

  mainWindow.on("ready-to-show", () => {
    mainWindow.show();
  });

  // SECURITY (XSS-002 前端XSS·外链交付): only hand http/https/mailto URLs to the OS shell.
  // `shell.openExternal` launches ANY registered URI scheme (file://, ms-msdt:, custom
  // protocols — Follina-class on Windows); a target=_blank anchor carrying an attacker-
  // influenceable URL (a web-source / tool-result card URL) would otherwise let a single
  // click launch a dangerous local handler. Unsafe schemes are denied + logged.
  mainWindow.webContents.setWindowOpenHandler((details) => {
    if (isSafeExternalUrl(details.url)) {
      void shell.openExternal(details.url);
    } else {
      console.warn(
        `[security] blocked openExternal for unsafe URL scheme: ${details.url}`,
      );
    }
    return { action: "deny" };
  });

  // SECURITY (XSS-004 前端XSS·导航逃逸): the SPA is HashRouter, so legitimate route changes
  // only mutate the URL hash and never fire will-navigate with a new document URL. Any
  // will-navigate to a URL outside the trusted renderer origin (prod: app://agentcore; dev:
  // the Vite server) is an attempted navigation away from the app — block it. Outbound
  // links go through setWindowOpenHandler above, not here.
  mainWindow.webContents.on("will-navigate", (event, url) => {
    const allowedBase = allowedNavigationBase();
    if (!url.startsWith(allowedBase)) {
      event.preventDefault();
      console.warn(`[security] blocked in-page navigation to: ${url}`);
    }
  });

  mainWindow.loadURL(rendererLoadBase());

  return mainWindow;
}

app.whenReady().then(async () => {
  // Windows 通知中心需要 AppUserModelId，否则 toast 静默失败。
  if (process.platform === "win32") {
    app.setAppUserModelId(WINDOWS_APP_USER_MODEL_ID);
  }
  registerAppProtocol();
  registerLogIpc();
  registerDeviceIdentityIpc();
  registerFsIpc();
  registerSidecarIpc();
  registerOutboxIpc();
  installAuthCookieFlushOnQuit();
  registerLocalStoreIpc();
  registerTerminalIpc();
  registerProcessIpc();
  registerPtyIpc();
  registerNotificationIpc();
  registerHostIpc();
  registerMcpIpc();
  registerBrowserIpc();
  registerFloatWindowIpc({
    getMainWindow: () => mainWindowRef,
    buildFloatUrl: (conversationId, tabId) =>
      `${rendererLoadBase()}${buildFloatHashRoute(conversationId, tabId)}`,
    allowedNavigationBase: allowedNavigationBase(),
    preloadPath: preloadPath(),
    icon,
  });
  // B-Arch-1: Bridge Ready is part of the control plane — await listen before
  // window/sidecar work so initialize/startTurn can hand out live credentials.
  try {
    const bridge = await startDesktopBrowserBridge();
    console.info(`[browser-bridge] ready at ${bridge.baseUrl}`);
  } catch (err) {
    console.warn("[browser-bridge] failed to start:", err);
  }
  const mainWindow = createWindow();
  // 自动更新随首个窗口创建后初始化一次（IPC 句柄全局唯一，不在 createWindow 内调用，
  // 以免 macOS 上 activate 重建窗口时重复注册）。
  initUpdater(mainWindow);
  // 清理上次会话遗留的孤儿会话文件（写盘后 meta 未落即退出）。内部走 meta 锁，
  // 与渲染进程的缓存写入串行，故不 await、不阻塞首屏。
  void sweepOrphanLocalStoreFiles();
  // 上次会话遗留的只读临时副本（fs:openTempFile）：副本得活到外部程序读完，只能等下次
  // 启动回收；只扫早于本次启动的目录，正在被打开的那份碰不到。
  void sweepOpenTempOrphans();

  app.on("activate", () => {
    // 只看主窗：真 OS 浮窗存活时仍应能重建主窗。
    if (!mainWindowRef || mainWindowRef.isDestroyed()) createWindow();
  });
});

app.on("before-quit", () => {
  destroyAllFloatWindows();
  void shutdownAllMcpSessions();
});

app.on("window-all-closed", () => {
  void shutdownAllMcpSessions();
  if (process.platform !== "darwin") {
    app.quit();
  }
});
