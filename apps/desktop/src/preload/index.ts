import {
  BROWSER_CHANNELS,
  type BrowserApi,
  type BrowserNavState,
  type BrowserOpenTabRequest,
} from "@shared/browser-contract";
import {
  DEVICE_IDENTITY_CHANNELS,
  type DeviceIdentityApi,
} from "@shared/device-identity-contract";
import {
  FLOAT_WINDOW_CHANNELS,
  type FloatWindowApi,
  type FloatWindowClosedPayload,
} from "@shared/float-window-contract";
import { HOST_CHANNELS, type HostApi } from "@shared/host-contract";
import {
  FS_CHANNELS,
  type FsApi,
  type FsChangedEvent,
  type FsResult,
  type StageAttachmentDest,
  type StagedAttachment,
} from "@shared/ipc-contract";
import {
  LOCAL_STORE_CHANNELS,
  type LocalStoreApi,
  type LocalStoreConversationPayload,
  type LocalStorePutShellMeta,
} from "@shared/local-store-contract";
import { LOG_CHANNELS, type LogApi } from "@shared/log-contract";
import { MCP_CHANNELS, type McpApi } from "@shared/mcp-contract";
import {
  NOTIFICATION_CHANNELS,
  type NotificationApi,
} from "@shared/notification-contract";
import {
  OUTBOX_CHANNELS,
  type OutboxApi,
  type OutboxSyncedPayload,
} from "@shared/outbox-contract";
import {
  PROCESS_CHANNELS,
  type ProcessApi,
  type ProcessEventPush,
} from "@shared/process-contract";
import {
  PTY_CHANNELS,
  type PtyApi,
  type PtyEventPush,
} from "@shared/pty-contract";
import {
  SIDECAR_CHANNELS,
  type SidecarApi,
  type SidecarEventPush,
  type SidecarFulfillPush,
  type SidecarStatusPush,
} from "@shared/sidecar-contract";
import { TERMINAL_CHANNELS, type TerminalApi } from "@shared/terminal-contract";
import {
  UPDATER_CHANNELS,
  type UpdaterApi,
  type UpdaterStatus,
} from "@shared/updater-contract";
import {
  WINDOW_CHANNELS,
  type WindowApi,
  type WindowFramePreset,
} from "@shared/window-contract";
import { contextBridge, ipcRenderer, webUtils } from "electron";

const deviceIdentityApi: DeviceIdentityApi = {
  getDeviceId: () => ipcRenderer.invoke(DEVICE_IDENTITY_CHANNELS.getDeviceId),
};

const fsApi: FsApi = {
  addRoot: () => ipcRenderer.invoke(FS_CHANNELS.addRoot),
  ensureDefaultRoot: () => ipcRenderer.invoke(FS_CHANNELS.ensureDefaultRoot),
  checkoutArchive: (archiveBase64) =>
    ipcRenderer.invoke(FS_CHANNELS.checkoutArchive, { archiveBase64 }),
  saveFile: (suggestedName, bytes) =>
    ipcRenderer.invoke(FS_CHANNELS.saveFile, { suggestedName, bytes }),
  openTempFile: (suggestedName, bytes) =>
    ipcRenderer.invoke(FS_CHANNELS.openTempFile, { suggestedName, bytes }),
  previewArchive: (archiveBase64, openRelPath) =>
    ipcRenderer.invoke(FS_CHANNELS.previewArchive, {
      archiveBase64,
      openRelPath,
    }),
  listRoots: () => ipcRenderer.invoke(FS_CHANNELS.listRoots),
  removeRoot: (rootId) =>
    ipcRenderer.invoke(FS_CHANNELS.removeRoot, { rootId }),
  grantSessionReadonlyRoot: (conversationIdOrParams, mode) => {
    const params =
      typeof conversationIdOrParams === "string"
        ? {
            conversationId: conversationIdOrParams,
            mode: mode ?? "readonly",
          }
        : {
            conversationId: conversationIdOrParams.conversationId,
            mode: conversationIdOrParams.mode ?? "readonly",
            ...(conversationIdOrParams.path
              ? { path: conversationIdOrParams.path }
              : {}),
            ...(conversationIdOrParams.wellKnown
              ? { wellKnown: conversationIdOrParams.wellKnown }
              : {}),
            ...(conversationIdOrParams.targetName
              ? { targetName: conversationIdOrParams.targetName }
              : {}),
          };
    return ipcRenderer.invoke(FS_CHANNELS.grantSessionReadonlyRoot, params);
  },
  listSessionReadonlyRoots: (conversationId) =>
    ipcRenderer.invoke(FS_CHANNELS.listSessionReadonlyRoots, {
      conversationId,
    }),
  revokeSessionReadonlyRoot: (conversationId, rootId) =>
    ipcRenderer.invoke(FS_CHANNELS.revokeSessionReadonlyRoot, {
      conversationId,
      rootId,
    }),
  clearSessionReadonlyRoots: (conversationId) =>
    ipcRenderer.invoke(FS_CHANNELS.clearSessionReadonlyRoots, {
      conversationId,
    }),
  adoptSessionRootAlias: (conversationId, rootId, alias) =>
    ipcRenderer.invoke(FS_CHANNELS.adoptSessionRootAlias, {
      conversationId,
      rootId,
      alias,
    }),
  listDir: (rootId, relPath) =>
    ipcRenderer.invoke(FS_CHANNELS.listDir, { rootId, relPath }),
  listFiles: (rootId, opts) =>
    ipcRenderer.invoke(FS_CHANNELS.listFiles, {
      rootId,
      ...(opts?.order ? { order: opts.order } : {}),
    }),
  readFile: (rootId, relPath) =>
    ipcRenderer.invoke(FS_CHANNELS.readFile, { rootId, relPath }),
  readTextFile: (rootId, relPath) =>
    ipcRenderer.invoke(FS_CHANNELS.readTextFile, { rootId, relPath }),
  writeFile: (rootId, relPath, input) =>
    ipcRenderer.invoke(FS_CHANNELS.writeFile, { rootId, relPath, input }),
  rename: (rootId, relPath, newName) =>
    ipcRenderer.invoke(FS_CHANNELS.rename, { rootId, relPath, newName }),
  move: (rootId, srcRelPath, destRelPath) =>
    ipcRenderer.invoke(FS_CHANNELS.move, { rootId, srcRelPath, destRelPath }),
  copy: (rootId, srcRelPath, destRelPath) =>
    ipcRenderer.invoke(FS_CHANNELS.copy, { rootId, srcRelPath, destRelPath }),
  create: (rootId, relPath, kind) =>
    ipcRenderer.invoke(FS_CHANNELS.create, { rootId, relPath, kind }),
  delete: (rootId, relPath) =>
    ipcRenderer.invoke(FS_CHANNELS.delete, { rootId, relPath }),
  watch: (rootId, relPath) =>
    ipcRenderer.invoke(FS_CHANNELS.watch, { rootId, relPath }),
  unwatch: (rootId, relPath) =>
    ipcRenderer.invoke(FS_CHANNELS.unwatch, { rootId, relPath }),
  onChanged: (cb) => {
    const listener = (_e: unknown, payload: FsChangedEvent) => cb(payload);
    ipcRenderer.on(FS_CHANNELS.changed, listener);
    return () => ipcRenderer.removeListener(FS_CHANNELS.changed, listener);
  },
  workspaceOp: (rootId, op, args, timeoutMs, correlation) =>
    ipcRenderer.invoke(FS_CHANNELS.workspaceOp, {
      rootId,
      op,
      args,
      ...(typeof timeoutMs === "number" ? { timeoutMs } : {}),
      ...(correlation?.conversationId
        ? { conversationId: correlation.conversationId }
        : {}),
      ...(correlation?.requestId ? { requestId: correlation.requestId } : {}),
    }),
  grantSessionRun: () => ipcRenderer.invoke(FS_CHANNELS.grantSessionRun),
  reveal: (rootId, relPath) =>
    ipcRenderer.invoke(FS_CHANNELS.reveal, { rootId, relPath }),
  openPath: (rootId, relPath) =>
    ipcRenderer.invoke(FS_CHANNELS.openPath, { rootId, relPath }),
  copyPath: (rootId, relPath) =>
    ipcRenderer.invoke(FS_CHANNELS.copyPath, { rootId, relPath }),
  trashPath: (rootId, relPath) =>
    ipcRenderer.invoke(FS_CHANNELS.trashPath, { rootId, relPath }),
  listWorkspaceTrash: (rootId) =>
    ipcRenderer.invoke(FS_CHANNELS.listWorkspaceTrash, { rootId }),
  restoreWorkspaceTrash: (rootId, entryId) =>
    ipcRenderer.invoke(FS_CHANNELS.restoreWorkspaceTrash, { rootId, entryId }),
  listWorkspaceVersions: (rootId, subpath) =>
    ipcRenderer.invoke(FS_CHANNELS.listWorkspaceVersions, { rootId, subpath }),
  deleteWorkspaceVersion: (rootId, subpath, versionId) =>
    ipcRenderer.invoke(FS_CHANNELS.deleteWorkspaceVersion, {
      rootId,
      subpath,
      versionId,
    }),
  pickAndStageAttachment: (dest) =>
    ipcRenderer.invoke(FS_CHANNELS.pickAndStageAttachment, { dest }),
  stageFromRoot: (rootId, relPath, dest) =>
    ipcRenderer.invoke(FS_CHANNELS.stageFromRoot, { rootId, relPath, dest }),
  stageDroppedFile: async (file, dest) => {
    let absPath = "";
    try {
      absPath = webUtils.getPathForFile(file);
    } catch {
      absPath = "";
    }
    if (absPath) {
      return ipcRenderer.invoke(FS_CHANNELS.stageFromAbsPath, {
        absPath,
        dest,
      }) as Promise<FsResult<StagedAttachment>>;
    }
    // 剪贴板截图等：File 无盘路径（Electron getPathForFile → ""）。按字节驻留。
    const maxBytes = 50 * 1024 * 1024;
    if (typeof file.size === "number" && file.size > maxBytes) {
      return {
        ok: false as const,
        reason: `文件超过 ${Math.round(maxBytes / (1024 * 1024))}MB 上限`,
        code: "invalid" as const,
      } satisfies FsResult<StagedAttachment>;
    }
    try {
      const buf = await file.arrayBuffer();
      return (await ipcRenderer.invoke(FS_CHANNELS.stageFromBytes, {
        name: file.name || "attachment",
        bytes: new Uint8Array(buf),
        mime: file.type || undefined,
        dest,
      })) as FsResult<StagedAttachment>;
    } catch {
      return {
        ok: false as const,
        reason: "无法读取该文件，请改用回形针选择",
        code: "invalid" as const,
      } satisfies FsResult<StagedAttachment>;
    }
  },
  finalizeStagedAttachment: (stagingId, dest: StageAttachmentDest) =>
    ipcRenderer.invoke(FS_CHANNELS.finalizeStagedAttachment, {
      stagingId,
      dest,
    }),
  consumeStagedBytes: (stagingId) =>
    ipcRenderer.invoke(FS_CHANNELS.consumeStagedBytes, { stagingId }),
  sweepStagingOrphans: (liveStagingIds) =>
    ipcRenderer.invoke(FS_CHANNELS.sweepStagingOrphans, { liveStagingIds }),
};

const sidecarApi: SidecarApi = {
  startTurn: (req) => ipcRenderer.invoke(SIDECAR_CHANNELS.startTurn, req),
  cancel: (req) => ipcRenderer.invoke(SIDECAR_CHANNELS.cancel, req),
  respond: (req) => ipcRenderer.invoke(SIDECAR_CHANNELS.respond, req),
  runRedirect: (req) => ipcRenderer.invoke(SIDECAR_CHANNELS.runRedirect, req),
  runStop: (req) => ipcRenderer.invoke(SIDECAR_CHANNELS.runStop, req),
  debateSteer: (req) => ipcRenderer.invoke(SIDECAR_CHANNELS.debateSteer, req),
  deliverMessage: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.deliverMessage, req),
  cancelQueuedTurn: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.cancelQueuedTurn, req),
  listQueuedTurns: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.listQueuedTurns, req),
  resume: (req) => ipcRenderer.invoke(SIDECAR_CHANNELS.resume, req),
  probe: (req) => ipcRenderer.invoke(SIDECAR_CHANNELS.probe, req),
  warmCodeIndex: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.warmCodeIndex, req),
  warmMcpDiscover: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.warmMcpDiscover, req),
  warmAccountRulesMemory: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.warmAccountRulesMemory, req),
  refreshLiveAccountRulesMemory: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.refreshLiveAccountRulesMemory, req),
  recovery: (req) => ipcRenderer.invoke(SIDECAR_CHANNELS.recovery, req),
  attach: (req) => ipcRenderer.invoke(SIDECAR_CHANNELS.attach, req),
  turnFilesDiff: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.turnFilesDiff, req),
  restoreTurnBaseline: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.restoreTurnBaseline, req),
  createWorkspaceVersion: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.createWorkspaceVersion, req),
  restoreWorkspaceVersion: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.restoreWorkspaceVersion, req),
  listBrowserSessions: (req) =>
    ipcRenderer.invoke(SIDECAR_CHANNELS.listBrowserSessions, req),
  onEvent: (cb) => {
    const listener = (_e: unknown, payload: SidecarEventPush) => cb(payload);
    ipcRenderer.on(SIDECAR_CHANNELS.event, listener);
    return () => ipcRenderer.removeListener(SIDECAR_CHANNELS.event, listener);
  },
  onFulfillFrame: (cb) => {
    const listener = (_e: unknown, payload: SidecarFulfillPush) => cb(payload);
    ipcRenderer.on(SIDECAR_CHANNELS.fulfill, listener);
    return () => ipcRenderer.removeListener(SIDECAR_CHANNELS.fulfill, listener);
  },
  onStatus: (cb) => {
    const listener = (_e: unknown, payload: SidecarStatusPush) => cb(payload);
    ipcRenderer.on(SIDECAR_CHANNELS.status, listener);
    return () => ipcRenderer.removeListener(SIDECAR_CHANNELS.status, listener);
  },
};

const localStoreApi: LocalStoreApi = {
  hasCache: () => ipcRenderer.invoke(LOCAL_STORE_CHANNELS.hasCache),
  getSnapshot: () => ipcRenderer.invoke(LOCAL_STORE_CHANNELS.getSnapshot),
  getConversation: (id: string) =>
    ipcRenderer.invoke(LOCAL_STORE_CHANNELS.getConversation, id),
  putOpenedConversation: (payload: LocalStoreConversationPayload) =>
    ipcRenderer.invoke(LOCAL_STORE_CHANNELS.putOpenedConversation, payload),
  putShellMeta: (meta: LocalStorePutShellMeta) =>
    ipcRenderer.invoke(LOCAL_STORE_CHANNELS.putShellMeta, meta),
  clear: () => ipcRenderer.invoke(LOCAL_STORE_CHANNELS.clear),
};

const outboxApi: OutboxApi = {
  flush: () => ipcRenderer.invoke(OUTBOX_CHANNELS.flush),
  flushTurn: (req) => ipcRenderer.invoke(OUTBOX_CHANNELS.flushTurn, req),
  status: () => ipcRenderer.invoke(OUTBOX_CHANNELS.status),
  onSynced: (cb) => {
    const listener = (_e: unknown, payload: OutboxSyncedPayload) => cb(payload);
    ipcRenderer.on(OUTBOX_CHANNELS.synced, listener);
    return () => ipcRenderer.removeListener(OUTBOX_CHANNELS.synced, listener);
  },
  authRefresh: () => ipcRenderer.invoke(OUTBOX_CHANNELS.authRefresh),
  persistAuthCookies: () =>
    ipcRenderer.invoke(OUTBOX_CHANNELS.persistAuthCookies),
};

const updaterApi: UpdaterApi = {
  configure: (apiBaseUrl) =>
    ipcRenderer.invoke(UPDATER_CHANNELS.configure, apiBaseUrl),
  check: () => ipcRenderer.invoke(UPDATER_CHANNELS.check),
  download: () => ipcRenderer.invoke(UPDATER_CHANNELS.download),
  openInstaller: () => ipcRenderer.invoke(UPDATER_CHANNELS.openInstaller),
  getStatus: () => ipcRenderer.invoke(UPDATER_CHANNELS.getStatus),
  onStatus: (cb) => {
    const listener = (_e: unknown, payload: UpdaterStatus) => cb(payload);
    ipcRenderer.on(UPDATER_CHANNELS.status, listener);
    return () => ipcRenderer.removeListener(UPDATER_CHANNELS.status, listener);
  },
};

const logApi: LogApi = {
  write: (entry) => ipcRenderer.send(LOG_CHANNELS.write, entry),
  readTail: () => ipcRenderer.invoke(LOG_CHANNELS.readTail),
};

const terminalApi: TerminalApi = {
  runBash: (input) => ipcRenderer.invoke(TERMINAL_CHANNELS.runBash, input),
  openShellAtRoot: (rootId, subpath) =>
    ipcRenderer.invoke(TERMINAL_CHANNELS.openShellAtRoot, rootId, subpath),
};

const processApi: ProcessApi = {
  list: (req) => ipcRenderer.invoke(PROCESS_CHANNELS.list, req),
  stop: (req) => ipcRenderer.invoke(PROCESS_CHANNELS.stop, req),
  read: (req) => ipcRenderer.invoke(PROCESS_CHANNELS.read, req),
  killConversation: (req) =>
    ipcRenderer.invoke(PROCESS_CHANNELS.killConversation, req),
  onEvent: (cb) => {
    const listener = (_e: unknown, payload: ProcessEventPush) => cb(payload);
    ipcRenderer.on(PROCESS_CHANNELS.event, listener);
    return () => ipcRenderer.removeListener(PROCESS_CHANNELS.event, listener);
  },
};

const ptyApi: PtyApi = {
  spawn: (req) => ipcRenderer.invoke(PTY_CHANNELS.spawn, req),
  input: (req) => ipcRenderer.invoke(PTY_CHANNELS.input, req),
  resize: (req) => ipcRenderer.invoke(PTY_CHANNELS.resize, req),
  kill: (req) => ipcRenderer.invoke(PTY_CHANNELS.kill, req),
  list: (req) => ipcRenderer.invoke(PTY_CHANNELS.list, req),
  read: (req) => ipcRenderer.invoke(PTY_CHANNELS.read, req),
  killConversation: (req) =>
    ipcRenderer.invoke(PTY_CHANNELS.killConversation, req),
  onEvent: (cb) => {
    const listener = (_e: unknown, payload: PtyEventPush) => cb(payload);
    ipcRenderer.on(PTY_CHANNELS.event, listener);
    return () => ipcRenderer.removeListener(PTY_CHANNELS.event, listener);
  },
};

const notificationApi: NotificationApi = {
  show: (input) => ipcRenderer.invoke(NOTIFICATION_CHANNELS.show, input),
  onClicked: (cb) => {
    const listener = (_e: unknown, payload: { conversationId?: string }) =>
      cb(payload);
    ipcRenderer.on(NOTIFICATION_CHANNELS.clicked, listener);
    return () =>
      ipcRenderer.removeListener(NOTIFICATION_CHANNELS.clicked, listener);
  },
};

const hostApi: HostApi = {
  runOp: (input) => ipcRenderer.invoke(HOST_CHANNELS.runOp, input),
};

const mcpApi: McpApi = {
  runOp: (input) => ipcRenderer.invoke(MCP_CHANNELS.runOp, input),
  listServers: () => ipcRenderer.invoke(MCP_CHANNELS.listServers),
  upsertServer: (server) =>
    ipcRenderer.invoke(MCP_CHANNELS.upsertServer, server),
  removeServer: (id) => ipcRenderer.invoke(MCP_CHANNELS.removeServer, id),
  setServerEnabled: (id, enabled) =>
    ipcRenderer.invoke(MCP_CHANNELS.setServerEnabled, id, enabled),
  testServer: (id) => ipcRenderer.invoke(MCP_CHANNELS.testServer, id),
};

const browserApi: BrowserApi = {
  show: (input) => ipcRenderer.invoke(BROWSER_CHANNELS.show, input),
  setBounds: (bounds) => ipcRenderer.send(BROWSER_CHANNELS.setBounds, bounds),
  hide: () => ipcRenderer.invoke(BROWSER_CHANNELS.hide),
  navigate: (input) => ipcRenderer.invoke(BROWSER_CHANNELS.navigate, input),
  openWorkspaceHtml: (input) =>
    ipcRenderer.invoke(BROWSER_CHANNELS.openWorkspaceHtml, input),
  reload: (pageId) => ipcRenderer.send(BROWSER_CHANNELS.reload, { pageId }),
  back: (pageId) => ipcRenderer.send(BROWSER_CHANNELS.back, { pageId }),
  forward: (pageId) => ipcRenderer.send(BROWSER_CHANNELS.forward, { pageId }),
  close: (pageId) => ipcRenderer.send(BROWSER_CHANNELS.close, { pageId }),
  closeConversation: (input) =>
    ipcRenderer.invoke(BROWSER_CHANNELS.closeConversation, input),
  openExternal: (input) =>
    ipcRenderer.invoke(BROWSER_CHANNELS.openExternal, input),
  onNavState: (cb) => {
    const listener = (_e: unknown, payload: BrowserNavState) => cb(payload);
    ipcRenderer.on(BROWSER_CHANNELS.navState, listener);
    return () =>
      ipcRenderer.removeListener(BROWSER_CHANNELS.navState, listener);
  },
  onOpenTab: (cb) => {
    const listener = (_e: unknown, payload: BrowserOpenTabRequest) =>
      cb(payload);
    ipcRenderer.on(BROWSER_CHANNELS.openTab, listener);
    return () => ipcRenderer.removeListener(BROWSER_CHANNELS.openTab, listener);
  },
};

const windowApi: WindowApi = {
  minimize: () => ipcRenderer.send(WINDOW_CHANNELS.minimize),
  maximize: () => ipcRenderer.send(WINDOW_CHANNELS.maximize),
  close: () => ipcRenderer.send(WINDOW_CHANNELS.close),
  applyFramePreset: (preset: WindowFramePreset) =>
    ipcRenderer.invoke(WINDOW_CHANNELS.applyFramePreset, preset),
  getFramePreset: () => ipcRenderer.invoke(WINDOW_CHANNELS.getFramePreset),
  setThemeSource: (theme) =>
    ipcRenderer.send(WINDOW_CHANNELS.setThemeSource, theme),
};

/** 真 OS 浮窗（方案 C）；主窗侧 open/dock/destroy + closed 订阅。 */
const floatWindowApi: FloatWindowApi = {
  open: (input) => ipcRenderer.invoke(FLOAT_WINDOW_CHANNELS.open, input),
  dock: (input) => ipcRenderer.invoke(FLOAT_WINDOW_CHANNELS.dock, input),
  destroy: (input) => ipcRenderer.invoke(FLOAT_WINDOW_CHANNELS.destroy, input),
  onClosed: (cb) => {
    const listener = (_e: unknown, payload: FloatWindowClosedPayload) =>
      cb(payload);
    ipcRenderer.on(FLOAT_WINDOW_CHANNELS.closed, listener);
    return () =>
      ipcRenderer.removeListener(FLOAT_WINDOW_CHANNELS.closed, listener);
  },
};

if (!process.contextIsolated) {
  throw new Error(
    "preload requires contextIsolation; refusing to mount APIs on window",
  );
}

try {
  contextBridge.exposeInMainWorld("deviceIdentityApi", deviceIdentityApi);
  contextBridge.exposeInMainWorld("fsApi", fsApi);
  contextBridge.exposeInMainWorld("sidecarApi", sidecarApi);
  contextBridge.exposeInMainWorld("outboxApi", outboxApi);
  contextBridge.exposeInMainWorld("localStoreApi", localStoreApi);
  contextBridge.exposeInMainWorld("updaterApi", updaterApi);
  contextBridge.exposeInMainWorld("logApi", logApi);
  contextBridge.exposeInMainWorld("terminalApi", terminalApi);
  contextBridge.exposeInMainWorld("processApi", processApi);
  contextBridge.exposeInMainWorld("ptyApi", ptyApi);
  contextBridge.exposeInMainWorld("notificationApi", notificationApi);
  contextBridge.exposeInMainWorld("hostApi", hostApi);
  contextBridge.exposeInMainWorld("mcpApi", mcpApi);
  contextBridge.exposeInMainWorld("browserApi", browserApi);
  contextBridge.exposeInMainWorld("windowApi", windowApi);
  contextBridge.exposeInMainWorld("floatWindowApi", floatWindowApi);
} catch (error) {
  console.error(error);
}
