// Production web client entry (P1 多端：web = 「云工作区」一等入口；前端技术与架构 §五).
//
// Installs the browser-runtime stubs for the four Electron globals and marks
// `window.__WEB__` (side-effect import, runs before ./main), then boots the real
// renderer unchanged — cookie auth in the browser, Bearer + 安全存储 in Capacitor
// (`markNative` when `Capacitor.isNativePlatform()`).
//
// Contrast main.web.tsx (offline preview), which additionally imports
// ./preview/markPreview to set `__WEB_PREVIEW__` and skip auth for `#/preview`.
import "./preview/browserStubs";
import "./lib/markNative";
import "./main";
