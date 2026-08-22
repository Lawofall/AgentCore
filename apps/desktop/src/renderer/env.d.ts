/// <reference types="vite/client" />

import type { AgentTownApi } from "@shared/agenttown-contract";
import type { BrowserApi } from "@shared/browser-contract";
import type { DeviceIdentityApi } from "@shared/device-identity-contract";
import type { FloatWindowApi } from "@shared/float-window-contract";
import type { HostApi } from "@shared/host-contract";
import type { FsApi } from "@shared/ipc-contract";
import type { LocalStoreApi } from "@shared/local-store-contract";
import type { LogApi } from "@shared/log-contract";
import type { McpApi } from "@shared/mcp-contract";
import type { NotificationApi } from "@shared/notification-contract";
import type { OutboxApi } from "@shared/outbox-contract";
import type { ProcessApi } from "@shared/process-contract";
import type { PtyApi } from "@shared/pty-contract";
import type { SidecarApi } from "@shared/sidecar-contract";
import type { TerminalApi } from "@shared/terminal-contract";
import type { UpdaterApi } from "@shared/updater-contract";
import type { WindowApi } from "@shared/window-contract";

declare global {
  interface ImportMetaEnv {
    readonly VITE_API_URL?: string;
    /** Capacitor 发版才为 true；缺 google-services.json 时不得 register 推送。 */
    readonly VITE_PUSH_ENABLED?: string;
    /** Dev-only auto-login credentials; see apps/desktop/.env.example. */
    readonly VITE_DEV_USERNAME?: string;
    readonly VITE_DEV_PASSWORD?: string;
  }

  interface Window {
    agentTownApi?: AgentTownApi;
    /** 履约通道 device_id（userData 持久化）；仅 Electron。 */
    deviceIdentityApi?: DeviceIdentityApi;
    fsApi: FsApi;
    sidecarApi: SidecarApi;
    /** Main-process outbox writeback + auth refresh (Electron only). */
    outboxApi?: OutboxApi;
    /** N4-A 只读离线：opened-conversation cache under userData/local-store (Electron only). */
    localStoreApi?: LocalStoreApi;
    /** Electron preload 注入；纯浏览器 / 单测环境可能缺失。 */
    updaterApi?: UpdaterApi;
    /** 结构化产品日志（落主进程 desktop.jsonl）；纯浏览器 / 单测可能缺失。 */
    logApi?: LogApi;
    /** bash 代码块「在终端运行」；纯浏览器 / 单测环境可能缺失。 */
    terminalApi?: TerminalApi;
    /** 后台进程（终端 tab）；纯浏览器 / 单测环境可能缺失。 */
    processApi?: ProcessApi;
    /** 用户交互 shell（终端 tab · M3）；纯浏览器 / 单测环境可能缺失。 */
    ptyApi?: PtyApi;
    /** OS 原生通知（窗口失焦时跨对话提醒）；纯浏览器 / 单测环境可能缺失。 */
    notificationApi?: NotificationApi;
    /** 本机 Host 能力（host_* ClientTool 回填）；纯浏览器 / 单测环境可能缺失。 */
    hostApi?: HostApi;
    /** 本机 MCP Client（stdio Server 配置 + ClientTool 回填）；纯浏览器 / 单测可能缺失。 */
    mcpApi?: McpApi;
    /** 右坞本机浏览器（LocalChromiumHost + openWorkspaceHtml）；仅 Electron；web / 单测可 mock。 */
    browserApi?: BrowserApi;
    windowApi: WindowApi;
    /** 真 OS 浮窗（方案 C）；仅 Electron；web / 单测可缺失。 */
    floatWindowApi?: FloatWindowApi;
    /** 由浏览器入口（生产 web 客户端 main.webapp.tsx / 离线预览 main.web.tsx → browserStubs）
     *  设置，标记「浏览器运行时、无原生 fs/sidecar/updater/window 能力」。能力代理
     *  （lib/capabilities）据此让本地专属功能降级、并使会话恒走云端。Electron 构建里始终缺失。 */
    __WEB__?: boolean;
    /** 仅由离线预览入口（main.web.tsx → markPreview）额外设置，标记「离线、无后端」运行，
     *  使 AuthGate 跳过认证 bootstrap（生产 web 客户端不设置，保留真实鉴权）。 */
    __WEB_PREVIEW__?: boolean;
    /** Capacitor 原生壳（main.webapp → markNative）。Bearer + 安全存储。 */
    __NATIVE__?: boolean;
    __NATIVE_PLATFORM__?: "android" | "ios";
  }
}
