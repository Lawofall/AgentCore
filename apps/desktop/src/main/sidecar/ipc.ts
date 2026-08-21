import {
  SIDECAR_CHANNELS,
  type SidecarAttachRequest,
  type SidecarAttachResponse,
  type SidecarCancelQueuedTurnRequest,
  type SidecarCancelRequest,
  type SidecarCreateWorkspaceVersionRequest,
  type SidecarDebateSteerRequest,
  type SidecarDeliverMessageRequest,
  type SidecarListBrowserSessionsRequest,
  type SidecarListBrowserSessionsResult,
  type SidecarListQueuedTurnsRequest,
  type SidecarListQueuedTurnsResult,
  type SidecarProbeRequest,
  type SidecarRecoveryRequest,
  type SidecarRecoveryResponse,
  type SidecarRespondRequest,
  type SidecarRestoreTurnBaselineRequest,
  type SidecarRestoreWorkspaceVersionRequest,
  type SidecarResumeRequest,
  type SidecarRunRedirectRequest,
  type SidecarRunStopRequest,
  type SidecarStartTurnRequest,
  type SidecarTurnFilesDiffRequest,
  type SidecarTurnFilesDiffResult,
  type SidecarTurnResult,
  type SidecarWarmAccountRulesMemoryRequest,
  type SidecarRefreshLiveAccountRulesMemoryRequest,
  type SidecarWarmCodeIndexRequest,
  type SidecarWarmMcpDiscoverRequest,
  type SidecarWorkspaceVersionResult,
} from "@shared/sidecar-contract";
import { app, ipcMain } from "electron";
import { getStoredRoot } from "../fs-service";
import {
  IpcInvalidArgsError,
  assertShape,
  ipcInvalidArgsLogFields,
} from "../ipc-validate";
import { logDesktop } from "../log-service";
import { SidecarManager } from "./manager";
import { resolveWorkspaceRoot } from "./workspace";

/**
 * sidecar IPC 边界校验：失败时先落 `sidecar.ipc_invalid_args`（desktop.jsonl +
 * dev stdout），再原样抛出——renderer 横幅可展示字段级原因，排查不再只靠 stderr。
 */
function assertSidecarShape(
  channel: string,
  payload: unknown,
  required: readonly string[],
  optionalStrings: readonly string[] = [],
  nullableIds: readonly string[] = [],
): void {
  try {
    assertShape(channel, payload, required, optionalStrings, nullableIds);
  } catch (err) {
    if (err instanceof IpcInvalidArgsError) {
      logDesktop({
        level: "error",
        event: "sidecar.ipc_invalid_args",
        fields: ipcInvalidArgsLogFields(err, payload),
      });
    }
    throw err;
  }
}

/** 注册全部 sidecar IPC handler。须在 app ready 后调用。 */
export function registerSidecarIpc(): void {
  const manager = new SidecarManager();

  // IPC-004（第五轮 IPC 权限面审计）：每个句柄进入业务前在边界结构校验寻址 / 标识类 string
  // 字段（rootId / turnId / …）+ 可选 subpath。畸形入参（仅来自被攻破的 renderer）抛
  // `IpcInvalidArgsError` → invoke reject，与本组句柄「拉不起 / 引擎异常即 reject 让 renderer
  // 降级」的契约一致。数据载荷（history / inference / result）仍由下游 / 引擎宽容消费。
  // 校验失败另落 `sidecar.ipc_invalid_args`（见 {@link assertSidecarShape}）。
  ipcMain.handle(
    SIDECAR_CHANNELS.startTurn,
    async (e, req: SidecarStartTurnRequest): Promise<SidecarTurnResult> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.startTurn,
        req,
        [
          "rootId",
          "conversationId",
          "turnId",
          "traceId",
          "userMessage",
          "userMessageId",
          "messageId",
        ],
        ["subpath", "userId"],
        ["folderId", "localRootId", "localSubpath"],
      );
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      return manager.startTurn(e.sender, req, workspaceRoot);
    },
  );

  ipcMain.handle(SIDECAR_CHANNELS.cancel, (_e, req: SidecarCancelRequest) => {
    assertSidecarShape(
      SIDECAR_CHANNELS.cancel,
      req,
      ["rootId", "turnId"],
      ["subpath", "conversationId", "reason"],
    );
    return manager.cancel(req);
  });

  ipcMain.handle(SIDECAR_CHANNELS.respond, (_e, req: SidecarRespondRequest) => {
    assertSidecarShape(
      SIDECAR_CHANNELS.respond,
      req,
      ["rootId", "requestId", "conversationId"],
      ["subpath"],
    );
    return manager.respond(req);
  });

  ipcMain.handle(
    SIDECAR_CHANNELS.runRedirect,
    (_e, req: SidecarRunRedirectRequest) => {
      assertSidecarShape(
        SIDECAR_CHANNELS.runRedirect,
        req,
        ["rootId", "conversationId", "executionId", "runId", "feedback"],
        ["subpath"],
      );
      return manager.runRedirect(req);
    },
  );

  ipcMain.handle(SIDECAR_CHANNELS.runStop, (_e, req: SidecarRunStopRequest) => {
    assertSidecarShape(
      SIDECAR_CHANNELS.runStop,
      req,
      ["rootId", "conversationId", "executionId"],
      ["subpath"],
      ["runId"],
    );
    return manager.runStop(req);
  });

  ipcMain.handle(
    SIDECAR_CHANNELS.debateSteer,
    (_e, req: SidecarDebateSteerRequest) => {
      assertSidecarShape(
        SIDECAR_CHANNELS.debateSteer,
        req,
        ["rootId", "conversationId", "executionId", "decision"],
        ["subpath", "focus", "ask", "askTarget"],
      );
      return manager.debateSteer(req);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.deliverMessage,
    (_e, req: SidecarDeliverMessageRequest) => {
      assertSidecarShape(
        SIDECAR_CHANNELS.deliverMessage,
        req,
        [
          "rootId",
          "conversationId",
          "content",
          "delivery",
          "userMessageId",
          "messageId",
          "traceId",
        ],
        ["subpath"],
      );
      return manager.deliverMessage(req);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.cancelQueuedTurn,
    (_e, req: SidecarCancelQueuedTurnRequest) => {
      assertSidecarShape(
        SIDECAR_CHANNELS.cancelQueuedTurn,
        req,
        ["rootId", "conversationId", "queueId"],
        ["subpath"],
      );
      return manager.cancelQueuedTurn(req);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.listQueuedTurns,
    (
      _e,
      req: SidecarListQueuedTurnsRequest,
    ): Promise<SidecarListQueuedTurnsResult> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.listQueuedTurns,
        req,
        ["rootId", "conversationId"],
        ["subpath"],
      );
      return manager.listQueuedTurns(req);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.resume,
    async (e, req: SidecarResumeRequest): Promise<SidecarTurnResult> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.resume,
        req,
        [
          "rootId",
          "conversationId",
          "messageId",
          "traceId",
          "decision",
          "note",
        ],
        ["subpath", "userMessageId", "userId"],
        ["folderId", "localRootId", "localSubpath"],
      );
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      return manager.resume(e.sender, req, workspaceRoot, req.inference);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.probe,
    async (_e, req: SidecarProbeRequest): Promise<void> => {
      assertSidecarShape(SIDECAR_CHANNELS.probe, req, ["rootId"], ["subpath"]);
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      await manager.probe(req.rootId, req.subpath ?? "", workspaceRoot);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.warmCodeIndex,
    async (_e, req: SidecarWarmCodeIndexRequest): Promise<void> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.warmCodeIndex,
        req,
        ["rootId"],
        ["subpath"],
      );
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      await manager.warmCodeIndex(req.rootId, req.subpath ?? "", workspaceRoot);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.warmMcpDiscover,
    async (_e, req: SidecarWarmMcpDiscoverRequest): Promise<void> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.warmMcpDiscover,
        req,
        ["rootId"],
        ["subpath", "userId"],
      );
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      await manager.warmMcpDiscover(
        req.rootId,
        req.subpath ?? "",
        workspaceRoot,
        { userId: req.userId },
      );
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.warmAccountRulesMemory,
    async (_e, req: SidecarWarmAccountRulesMemoryRequest): Promise<void> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.warmAccountRulesMemory,
        req,
        ["rootId"],
        ["subpath", "userId"],
        ["folderId"],
      );
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      await manager.warmAccountRulesMemory(
        req.rootId,
        req.subpath ?? "",
        workspaceRoot,
        {
          folderId: req.folderId,
          accountAuth: req.accountAuth,
          userId: req.userId,
        },
      );
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.refreshLiveAccountRulesMemory,
    async (
      _e,
      req: SidecarRefreshLiveAccountRulesMemoryRequest,
    ): Promise<void> => {
      const payload = req ?? {};
      assertSidecarShape(
        SIDECAR_CHANNELS.refreshLiveAccountRulesMemory,
        payload,
        [],
        ["userId"],
      );
      await manager.refreshLiveAccountRulesMemory({
        accountAuth: payload.accountAuth,
        userId: payload.userId,
      });
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.recovery,
    (_e, req: SidecarRecoveryRequest): Promise<SidecarRecoveryResponse> => {
      assertSidecarShape(SIDECAR_CHANNELS.recovery, req, ["conversationId"]);
      return manager.recovery(req);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.attach,
    (e, req: SidecarAttachRequest): SidecarAttachResponse => {
      assertSidecarShape(SIDECAR_CHANNELS.attach, req, ["conversationId"]);
      return manager.attach(e.sender, req);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.turnFilesDiff,
    async (
      _e,
      req: SidecarTurnFilesDiffRequest,
    ): Promise<SidecarTurnFilesDiffResult> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.turnFilesDiff,
        req,
        ["rootId", "messageId"],
        ["subpath"],
      );
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      return manager.turnFilesDiff(req, workspaceRoot);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.restoreTurnBaseline,
    async (_e, req: SidecarRestoreTurnBaselineRequest): Promise<void> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.restoreTurnBaseline,
        req,
        ["rootId", "snapshotId"],
        ["subpath"],
      );
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      await manager.restoreTurnBaseline(req, workspaceRoot);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.createWorkspaceVersion,
    async (
      _e,
      req: SidecarCreateWorkspaceVersionRequest,
    ): Promise<SidecarWorkspaceVersionResult> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.createWorkspaceVersion,
        req,
        ["rootId", "name"],
        ["subpath"],
      );
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      return manager.createWorkspaceVersion(req, workspaceRoot);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.restoreWorkspaceVersion,
    async (
      _e,
      req: SidecarRestoreWorkspaceVersionRequest,
    ): Promise<SidecarWorkspaceVersionResult> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.restoreWorkspaceVersion,
        req,
        ["rootId", "versionId"],
        ["subpath"],
      );
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      return manager.restoreWorkspaceVersion(req, workspaceRoot);
    },
  );

  ipcMain.handle(
    SIDECAR_CHANNELS.listBrowserSessions,
    async (
      _e,
      req: SidecarListBrowserSessionsRequest,
    ): Promise<SidecarListBrowserSessionsResult> => {
      assertSidecarShape(
        SIDECAR_CHANNELS.listBrowserSessions,
        req,
        ["rootId", "conversationId"],
        ["subpath"],
      );
      const root = await getStoredRoot(req.rootId);
      if (!root) throw new Error("本地目录未授权或已移除");
      const workspaceRoot = await resolveWorkspaceRoot(
        root.absPath,
        req.subpath,
      );
      return manager.listBrowserSessions(req, workspaceRoot);
    },
  );

  app.on("before-quit", () => manager.disposeAll());
}
