/**
 * 运行时能力描述符（cross-platform-frontend §7：web/手机 = 桌面 − 物理做不到的能力层）。
 *
 * 桌面渲染层跑在两种运行时：Electron 外壳（完整原生能力）与纯浏览器（生产 web 客户端
 * `main.webapp.tsx`、离线预览 `main.web.tsx`）。两个浏览器入口都给那四个 preload 全局装了
 * 无害桩并置 `window.__WEB__`，所以「全局是否存在」不再是「我是不是桌面」的真信号。一律经
 * 这些帮手读能力，别再直接嗅探 `window.fsApi` / `window.sidecarApi` 判端。
 */

/** 当前是否跑在浏览器 web 运行时（生产 web 客户端或离线预览），而非 Electron 外壳。 */
export function isWebRuntime(): boolean {
  return typeof window !== "undefined" && window.__WEB__ === true;
}

/** Capacitor 原生壳（Android / iOS WebView）。与 `__WEB__` 可同时为真。 */
export function isNativeRuntime(): boolean {
  return typeof window !== "undefined" && window.__NATIVE__ === true;
}

/** 是否是「生产 web 客户端」运行时（浏览器里跑真实鉴权，且非离线预览 #/preview）。用于给
 *  浏览器版单独裁剪只在 Electron/预览下才有意义的窗口外壳——顶栏（拖拽区/窗口控件）在浏览器里
 *  纯属多余高度，改由侧栏顶部承载品牌/折叠。搜索假入口两端都在侧栏。离线预览仍保留顶栏。 */
export function isWebClient(): boolean {
  return (
    isWebRuntime() &&
    !(typeof window !== "undefined" && window.__WEB_PREVIEW__ === true)
  );
}

/** 本地文件工作区（授权根、读写磁盘、@ 提及本地文件）——仅桌面，web 降级关闭。 */
export function hasLocalFiles(): boolean {
  return typeof window !== "undefined" && !!window.fsApi && !isWebRuntime();
}

/** 原生「另存为」落盘（fs:saveFile：主进程保存对话框 + 原子写）——仅桌面外壳。
 *  Electron 不支持 `<a download>` + blob:（不触发 will-download、blob: 导航被
 *  will-navigate 守卫拦截），故桌面下载须走主进程；web 的 saveBlob 走 anchor 下载。 */
export function hasNativeSave(): boolean {
  return (
    typeof window !== "undefined" && !!window.fsApi?.saveFile && !isWebRuntime()
  );
}

/** 本地引擎（sidecar，在用户机器上跑回合）——仅桌面，web 恒走云端 SSE。 */
export function hasLocalEngine(): boolean {
  return (
    typeof window !== "undefined" && !!window.sidecarApi && !isWebRuntime()
  );
}

/** 自绘无边框窗口控件（最小化 / 最大化 / 关闭）——仅桌面外壳；web 用浏览器自带窗口
 *  chrome（macOS 桌面用原生交通灯，由组件内 isMac 再分流）。 */
export function hasWindowControls(): boolean {
  return !isWebRuntime();
}

/** 应用内自动更新（主进程 electron-updater）——仅桌面外壳；web 客户端随刷新拿到新版。 */
export function hasAutoUpdater(): boolean {
  return !isWebRuntime();
}

/** AgentTown 独立客户端启动器（写 session.json + spawn）——仅 Electron 外壳。 */
export function hasAgentTownLauncher(): boolean {
  return (
    typeof window !== "undefined" && !!window.agentTownApi && !isWebRuntime()
  );
}

/** 应用内「完整预览」（workspace:// 代理会话工作区字节，右坞 BrowserPanel 本机页）——
 *  仅 Electron 外壳（需 browserApi.openWorkspaceHtml）；web 降级不暴露。云端会话工作区专属。 */
export function hasInAppPreview(): boolean {
  return (
    typeof window !== "undefined" &&
    !!window.browserApi?.openWorkspaceHtml &&
    !isWebRuntime()
  );
}

/** 聊天 bash 代码块「在终端运行」——仅 Electron 外壳。 */
export function hasTerminalRun(): boolean {
  return (
    typeof window !== "undefined" && !!window.terminalApi && !isWebRuntime()
  );
}

/** 本地工作区「在终端打开」——需 terminalApi + 本地文件能力。 */
export function hasWorkspaceShell(): boolean {
  return hasLocalFiles() && !!window.terminalApi?.openShellAtRoot;
}

/** OS 原生通知（Electron Notification API）；web 无。 */
export function hasNativeNotification(): boolean {
  return (
    typeof window !== "undefined" && !!window.notificationApi && !isWebRuntime()
  );
}
