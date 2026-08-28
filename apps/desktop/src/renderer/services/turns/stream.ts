import {
  bumpConversationCache,
  getConversations,
  restoreConversationCache,
} from "@/hooks/useConversations";
import { hasLocalEngine } from "@/lib/capabilities";
import {
  StreamError,
  describeStreamError,
  isUnstartedSendRefusal,
  streamErrorAction,
} from "@/lib/errors";
import { logEvent } from "@/lib/log";
import type { SupportDiagnosticIds } from "@/lib/supportDiagnostics";
import { markSidecarUnhealthy, probeSidecar } from "@/services/sidecarHealth";
import {
  isSidecarEnabled,
  resolveConversationLocalTarget,
  resolveSidecarRoot,
} from "@/services/sidecarRouting";
import {
  type OutgoingAgentMention,
  type OutgoingAttachment,
  type TurnCommitReport,
  streamConversation,
} from "@/services/streamConversation";
import { streamConversationViaSidecar } from "@/services/streamConversationViaSidecar";
import { traceTurnEnd, traceTurnMilestone } from "@/services/turnTrace";
import { restoreComposerDraft } from "@/stores/composer";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import {
  beginTurnPreflight,
  enterTurnStreaming,
  throwIfCannotOpenStream,
} from "@/stores/conversation/turnPhaseActions";
import { clearInteractionPrompts } from "@/stores/interactionPrompts";
import { dismissRecoverableHints } from "./dismissRecovery";
import {
  finalizeGeneratingIfNeeded,
  finalizeHonestStopAbort,
  isAbort,
  isTransportDrop,
} from "./helpers";
import {
  cancelRejoinLiveTurn,
  rejoinLiveTurn,
  settleOrphanEmptyAssistants,
} from "./recovery";
import { runRegenerate } from "./regenerate";
import { claimPrimaryStream, releasePrimaryStream } from "./streamOwnership";
import { inspectZeroOutputSendRollback } from "./zeroOutputSendRollback";

export interface SendTurnSpec {
  conversationId: string;
  content: string;
  attachments: OutgoingAttachment[];
  agentMentions?: OutgoingAgentMention[];
  /** Optimistic client id of the user bubble (already added to the store). */
  optimisticUserId: string;
  /** 必填分流；空闲开跑传 ``steer``。 */
  delivery?: "steer" | "queue";
}

function setExecutionVia(
  conversationId: string,
  via: "sidecar" | "cloud_bridge" | null,
): void {
  useConversationStore.getState().setExecutionVia(via, conversationId);
}

/** 云端分支原因——写入 turnTrace + desktop.jsonl，对照服务端 via=cloud。 */
type CloudPathReason =
  | "switch_off"
  | "no_local_engine"
  | "probe_unhealthy"
  | "probe_cache_bad"
  | "no_local_target"
  | "sidecar_fallback";

function resolveCloudPathReason(args: {
  hadSidecarTarget: boolean;
  probeHealthy: boolean | null;
  probeProbed: boolean | null;
}): CloudPathReason {
  if (!hasLocalEngine()) return "no_local_engine";
  if (!isSidecarEnabled()) return "switch_off";
  if (args.hadSidecarTarget && args.probeHealthy === false) {
    return args.probeProbed ? "probe_unhealthy" : "probe_cache_bad";
  }
  return "no_local_target";
}

function logStreamPath(
  conversationId: string,
  via: "sidecar" | "cloud",
  reason: string,
  extra?: Record<string, unknown>,
): void {
  const fields = { conversation_id: conversationId, via, reason, ...extra };
  traceTurnMilestone(conversationId, "stream_path", { via, reason, ...extra });
  // 持久化到 desktop.jsonl（非仅 DEV 控制台 opt-in），便于对照服务端 via=cloud。
  logEvent("info", "turn.stream_path", fields);
}

/**
 * Stream a freshly-sent user message.
 *
 * The user bubble is added optimistically by the caller before this runs. On a
 * transport failure it raises an error banner (no one-click re-send). Once the
 * transport reports this send committed a turn (cloud: `turn_saved`; sidecar:
 * outbox flush), a later regenerate from the saved message is the
 * persistence-aware re-run path — resending would duplicate the user turn.
 *
 * 发送即有流：POST 恒返回 SSE；in-flight 时先到 ``turn_queued``（dispatch 呈现
 * 「已排队」），drain 后同连接续流——不再有 202 JSON / 另行 attach 守望。
 */
export type SendTurnResult = {
  unstartedRefusal: boolean;
  supportPack?: SupportDiagnosticIds;
};

function rollbackUnstartedOptimisticTurn(
  conversationId: string,
  userId: string,
): void {
  const store = useConversationStore.getState();
  store.truncateAfter(userId, conversationId);
  store.removeMessage(userId, conversationId);
  store.setGenerating(false, conversationId);
  store.setTurnPhase("idle", conversationId);
  store.setWaitingForWorkspaceLock(false, conversationId);
}

function surfaceTurnBanner(conversationId: string, err: unknown): void {
  const msg = describeStreamError(err);
  if (msg) {
    useConversationStore
      .getState()
      .setError(msg, null, conversationId, streamErrorAction(err));
  }
}

function streamErrorFromZeroOutput(code: string, message: string): StreamError {
  return new StreamError("http", undefined, {
    code,
    serverMessage: message || undefined,
  });
}

function thrownErrorCode(err: unknown): string | undefined {
  return err instanceof StreamError ? err.code : undefined;
}

export async function sendTurn(spec: SendTurnSpec): Promise<SendTurnResult> {
  const {
    conversationId,
    content,
    attachments,
    agentMentions = [],
    optimisticUserId,
    delivery = "steer",
  } = spec;
  const store = useConversationStore.getState();
  // A new send takes the stream — stop GET-attach retries so we never race a
  // rejoin attach against this POST (that would double-fold, not double-run).
  cancelRejoinLiveTurn(conversationId);
  // Every turn write routes to this conversation's slice by id (not the active
  // key), so a turn keeps streaming into its own bubble after the user switches
  // away to another conversation.
  store.clearError(conversationId);

  // Implicit「忽略」: a new turn dismisses recoverable 救火 hints
  // (audit + session UI latch) without clearing the execution projection.
  dismissRecoverableHints(conversationId);

  // Orphan empty placeholder (1a69f9dc): prior incomplete/streaming blank must
  // become「已中断」before we append the new user→assistant pair.
  settleOrphanEmptyAssistants(conversationId);

  // Snapshot the pre-bump position so we can undo the optimistic bump if the
  // send fails before the server ever persisted the turn.
  const beforeBump = getConversations();
  const origIndex = beforeBump.findIndex((c) => c.id === conversationId);
  const origUpdatedAt = origIndex >= 0 ? beforeBump[origIndex].updatedAt : null;
  bumpConversationCache(conversationId);

  // Persisted already? Then the optimistic id was swapped out — regenerate from
  // the saved user message rather than resending (which would duplicate it).
  const stillOptimistic = getRuntime(conversationId).messages.some(
    (m) => m.id === optimisticUserId,
  );
  if (!stillOptimistic) {
    const lastUser = [...getRuntime(conversationId).messages]
      .reverse()
      .find((m) => m.role === "user");
    if (lastUser) {
      await runRegenerate(lastUser.id);
      return { unstartedRefusal: false };
    }
  }

  // Fresh attempt: drop any partial assistant bubble left by a failed try
  // (no-op on the first send, where the user bubble is already last).
  store.truncateAfter(optimisticUserId, conversationId);

  // Open the assistant bubble now (即时反馈), before the POST even resolves —
  // mirrors runRegenerate. This flips `isGenerating` on immediately so the
  // composer shows the stop button and the bubble shows a "Thinking…" indicator
  // during prepare/TTFT before the first content frame. A′: kickoff no longer
  // holds folder workspace_lock — 不得静默等锁. Residual write-lock short waits
  // emit ``workspace_lock_wait`` so the bubble shows「等待工作区…」instead of
  // faking Thinking…. In-flight 同对话排队时 ``turn_queued`` 先到——仅 QueuedTurnsBar.
  store.createAssistantMessage(conversationId);

  const ac = new AbortController();
  store.setAbort(ac, conversationId);
  beginTurnPreflight(conversationId);
  // 探活窗口起即占主路——midFlight 排队缓冲等到本回合整段泵（含 finally）释放。
  const primaryToken = claimPrimaryStream(conversationId);
  const turnCommit: TurnCommitReport = { committed: false };
  try {
    traceTurnMilestone(conversationId, "send_start");
    // 路由（双模式工作区 §7.2）：本机传统默认同侧 sidecar =
    //   有本地引擎 + 未显式强制关（sidecarPreference!=="off"；unset 不挡）+ 会话绑本机根。
    // 贴文件不改场地：区内引用 / 区外复制进 attachments/ 都跟绑定走。
    // 云链路：纯云会话 / 显式强制关 / 探活失败。点名是 prompt 软提示，不挡本机。
    // 勿把 unset→SIDECAR_DEFAULT_ENABLED 误读成「整段过桥」。resolveSidecarRoot 早退不 probe；
    // 健康由下方 probe 仅在有 target 时收敛。
    const sidecarTarget = await resolveSidecarRoot(conversationId);
    throwIfCannotOpenStream(conversationId, ac.signal);
    traceTurnMilestone(conversationId, "sidecar_resolve", {
      target: sidecarTarget
        ? { rootId: sidecarTarget.rootId, subpath: sidecarTarget.subpath }
        : null,
    });
    // 首次真正走 sidecar 前探活一次（探活增强）：拉起进程 + 握手验证本机环境能起得来。环境起
    // 不来则本轮落到下方云分支；`probeSidecar` 已按根记下 `bad`（带 TTL）。命中缓存时
    // probed:false——仍走云，但须可感知（节流 toast + executionVia），禁止整会话完全静默。
    const probe = sidecarTarget ? await probeSidecar(sidecarTarget) : null;
    throwIfCannotOpenStream(conversationId, ac.signal);
    if (probe) {
      traceTurnMilestone(conversationId, "sidecar_probe", {
        healthy: probe.healthy,
        probed: probe.probed,
      });
    }
    if (sidecarTarget && probe?.healthy) {
      setExecutionVia(conversationId, "sidecar");
      logStreamPath(conversationId, "sidecar", "probe_ok", {
        root_id: sidecarTarget.rootId,
        subpath: sidecarTarget.subpath,
      });
      try {
        throwIfCannotOpenStream(conversationId, ac.signal);
        enterTurnStreaming(conversationId);
        await streamConversationViaSidecar({
          conversationId,
          rootId: sidecarTarget.rootId,
          subpath: sidecarTarget.subpath,
          content,
          optimisticUserId,
          attachments,
          agentMentions,
          signal: ac.signal,
          turnCommit,
        });
      } catch (sidecarErr) {
        // 探活已过、但回合「启动期」仍失败的边缘（拉不起 / 握手失败，一个事件都没派发 →
        // recoverable）：本轮还没产生任何输出 / 副作用，故安全改走云链路重跑。同时标记
        // 该根坏 → 后续回合在 TTL 内命中 bad 缓存走云（与探活共用同一「记坏」出口）。
        // 中途失败（已流式 / 已调工具）与用户停止不在此列——照常抛给下方通用处理走
        // 「本地引擎出错」横幅 + 重试，绝不重复已发生的副作用。
        if (
          !(sidecarErr instanceof StreamError) ||
          sidecarErr.kind !== "sidecar" ||
          !sidecarErr.recoverable
        ) {
          throw sidecarErr;
        }
        const fallbackDetail =
          sidecarErr.serverMessage?.trim() || "本地引擎未能启动";
        markSidecarUnhealthy(sidecarTarget, fallbackDetail);
        setExecutionVia(conversationId, "cloud_bridge");
        store.truncateAfter(optimisticUserId, conversationId);
        store.createAssistantMessage(conversationId);
        beginTurnPreflight(conversationId);
        throwIfCannotOpenStream(conversationId, ac.signal);
        logStreamPath(conversationId, "cloud", "sidecar_fallback", {
          root_id: sidecarTarget.rootId,
          detail: fallbackDetail,
        });
        enterTurnStreaming(conversationId);
        await streamConversation({
          conversationId,
          content,
          attachments,
          agentMentions,
          delivery,
          signal: ac.signal,
          turnCommit,
        });
      }
    } else {
      // 云链路：探活失败 / bad 缓存 / 显式强制关 / 纯云会话。
      // 绑本机工作区却走云 = 云端过桥 → 写 executionVia（ComposerCloudBridgeHint）。
      const bridging =
        sidecarTarget !== null ||
        (await resolveConversationLocalTarget(conversationId)) !== null;
      setExecutionVia(conversationId, bridging ? "cloud_bridge" : null);
      const reason = resolveCloudPathReason({
        hadSidecarTarget: sidecarTarget !== null,
        probeHealthy: probe ? probe.healthy : null,
        probeProbed: probe ? probe.probed : null,
      });
      logStreamPath(conversationId, "cloud", reason, {
        bridging,
        root_id: sidecarTarget?.rootId ?? null,
        probe_detail: probe?.detail ?? null,
      });
      // 本地意向已是会话状态（Conversation.local_container_root_id，建会话时定型，
      // 工作区对称化 D1a），服务端据此在裸聊首次产文件时懒建本地 / 云端文件夹——
      // 回合不再携带容器根。
      throwIfCannotOpenStream(conversationId, ac.signal);
      enterTurnStreaming(conversationId);
      await streamConversation({
        conversationId,
        content,
        attachments,
        agentMentions,
        delivery,
        signal: ac.signal,
        turnCommit,
      });
    }
    const zero = inspectZeroOutputSendRollback(
      conversationId,
      turnCommit.committed,
    );
    if (zero) {
      // SSE error 后 stream 常 resolve：本发已提交 + 空失败 + Class B 码也要回滚。
      rollbackUnstartedOptimisticTurn(conversationId, zero.userId);
      surfaceTurnBanner(
        conversationId,
        streamErrorFromZeroOutput(zero.error.code, zero.error.message),
      );
      traceTurnEnd(conversationId, "error");
      return { unstartedRefusal: true, supportPack: zero.supportPack };
    }
    traceTurnEnd(conversationId, "ok");
    return { unstartedRefusal: false };
  } catch (err) {
    if (isAbort(err)) {
      finalizeHonestStopAbort(conversationId);
      traceTurnEnd(conversationId, "abort");
      return { unstartedRefusal: false };
    }
    // A mid-stream drop no longer means the turn died (1a: it runs detached) —
    // rejoin it live (1b) rather than resending, which would duplicate the turn.
    // (A sidecar engine failure is kind "sidecar", not "network", so a local turn
    // skips this and keeps its resend banner. A *startup* sidecar failure was
    // already rerouted to cloud upstream (阶段二), so one reaching here is
    // necessarily mid-run — never auto-rerouted, to avoid repeating side effects.)
    if (isTransportDrop(err) && (await rejoinLiveTurn(conversationId))) {
      traceTurnEnd(conversationId, "ok");
      return { unstartedRefusal: false };
    }
    // A failed turn never delivers `approval_resolved`; drop this conversation's
    // paused prompt (other conversations keep theirs).
    clearInteractionPrompts(conversationId);
    // If the turn never committed (no `turn_saved` / outbox flush this send),
    // the server order never changed — undo the optimistic bump.
    if (!turnCommit.committed && origIndex >= 0 && origUpdatedAt !== null) {
      restoreConversationCache(conversationId, origIndex, origUpdatedAt);
    }
    const unstartedRefusal =
      !turnCommit.committed && isUnstartedSendRefusal(err);
    const zero = unstartedRefusal
      ? null
      : inspectZeroOutputSendRollback(
          conversationId,
          turnCommit.committed,
          thrownErrorCode(err),
        );
    if (unstartedRefusal) {
      // 发送当没发生：撤乐观用户泡 + 空助手泡，phase 回 idle（failed 会挡下一发）。
      rollbackUnstartedOptimisticTurn(conversationId, optimisticUserId);
    } else if (zero) {
      rollbackUnstartedOptimisticTurn(conversationId, zero.userId);
    } else {
      finalizeGeneratingIfNeeded(conversationId);
    }
    surfaceTurnBanner(conversationId, err);
    traceTurnEnd(conversationId, "error");
    return {
      unstartedRefusal: unstartedRefusal || zero != null,
      ...(zero ? { supportPack: zero.supportPack } : {}),
    };
  } finally {
    // 仅清自己的 abort——midFlight 排队续流可能已接手同一会话的 abort 槽。
    if (getRuntime(conversationId).abort === ac) {
      useConversationStore.getState().setAbort(null, conversationId);
    }
    releasePrimaryStream(conversationId, primaryToken);
  }
}

/** 续写被截断的回答 (对话基础功能补齐): the latest reply ended early (用户叫停 / 达最大轮次),
 * so「继续生成」sends a minimal continuation turn — with the transcript in context, the model
 * picks up where it left off. Mirrors the composer's optimistic-send shape (add the user
 * bubble, then stream). No-op while a
 * turn is already streaming. */
export async function continueTurn(conversationId: string): Promise<void> {
  if (getRuntime(conversationId).isGenerating) return;
  const userMsgId = crypto.randomUUID();
  useConversationStore.getState().addMessage(
    {
      id: userMsgId,
      role: "user",
      content: "继续",
      createdAt: new Date().toISOString(),
      executionId: null,
      isStreaming: false,
    },
    conversationId,
  );
  const result = await sendTurn({
    conversationId,
    content: "继续",
    attachments: [],
    optimisticUserId: userMsgId,
  });
  if (result.unstartedRefusal) {
    restoreComposerDraft(conversationId, {
      value: "继续",
      attachments: [],
      agentMentions: [],
    });
  }
}
