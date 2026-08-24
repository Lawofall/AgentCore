import {
  applyDeletedConversationLocally,
  patchConversationCache,
  upsertConversationFront,
} from "@/hooks/useConversations";
import {
  type MessageDelivery,
  resolveDefaultDelivery,
} from "@/lib/composerDelivery";
import { confirmSendDespitePendingIfNeeded } from "@/lib/composerPendingHint";
import {
  forgetDraftRequestId,
  pinDraftRequestId,
  resolveDraftRequestId,
} from "@/lib/draftRequestId";
import { isReadOnlyOffline } from "@/lib/offlineMode";
import type { SupportDiagnosticIds } from "@/lib/supportDiagnostics";
import { notifyError } from "@/lib/toast";
import { api } from "@/services/api";
import {
  deleteConversation,
  provisionalConversationTitle,
  requestAutoTitle,
} from "@/services/conversations";
import { loadLatestWindow } from "@/services/messages";
import { getLastUsedProfileId } from "@/services/models";
import {
  type PermissionAxes,
  resolveDefaultPermissionAxes,
  setComposerDraftAxes,
} from "@/services/permissionAxes";
import { resolveSidecarRoot } from "@/services/sidecarRouting";
import type { OutgoingAgentMention } from "@/services/streamConversation";
import { sendTurn } from "@/services/turns";
import { sendMidFlightMessage } from "@/services/turns/midFlight";
import {
  draftKeyFor,
  restoreComposerDraft,
  useComposerDraftStore,
} from "@/stores/composer";
import {
  acquireComposerSendLatch,
  moveComposerSendLatch,
  releaseComposerSendLatch,
  useComposerSendPhase,
} from "@/stores/composerSend";
import {
  clearComposerSendError,
  setComposerSendError,
} from "@/stores/composerSendError";
import {
  getActiveRuntime,
  getRuntime,
  useConversationStore,
} from "@/stores/conversation";
import { useFoldersStore } from "@/stores/folders";
import { type Dispatch, type SetStateAction, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  forgetAttachmentUpload,
  peekAttachmentRecoverBlob,
} from "./attachmentUploads";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "./composerAttachments";
import { MAX_AGENT_MENTIONS } from "./composerAttachments";
import { dispatchBackgroundTask } from "./dispatchBackgroundTask";
import { settleAttachments } from "./settleAttachments";

/**
 * Local-first parallel title mint after the first user message.
 *
 * Gates on {@link resolveSidecarRoot} (same as turn routing), not the
 * background-handoff `isLocal` flag — drafts have no conversationId yet so that
 * flag stays false and would skip mint. Cloud turns keep SSE
 * `schedule_title_generation`; failure here leaves the provisional truncation.
 */
export function scheduleLocalAutoTitle(
  conversationId: string,
  userMessage: string,
): void {
  void resolveSidecarRoot(conversationId).then((target) => {
    if (!target) return;
    void requestAutoTitle(conversationId, userMessage).then((title) => {
      if (title) patchConversationCache(conversationId, { title });
    });
  });
}

function composerSendErrorFromRuntime(conversationId: string) {
  const rt = getRuntime(conversationId);
  return rt.error ? { message: rt.error, action: rt.errorAction } : null;
}

/**
 * 发送当没发生：已删会话的 ``workspacePath`` 不能拿来跳过再驻留；
 * ``stagingId`` 往往已被 consume，改用还握着的 File。
 */
function attachmentsForUnstartedRetry(
  pending: readonly PendingAttachment[],
): PendingAttachment[] {
  return pending.map((a) => ({
    ...a,
    workspacePath: undefined,
    fileBlob: a.fileBlob ?? peekAttachmentRecoverBlob(a.id),
  }));
}

/**
 * 开跑前拒绝：首发空会话拆回 `/` 草稿；跟发只还当前 cid。
 * 仅 `createdNew && 回滚后 messages.length===0` 才拆；删失败则留在 cid。
 */
async function restoreAfterUnstartedRefusal({
  conversationId,
  createdNew,
  createdFolderId,
  draft,
  navigate,
  supportPack,
}: {
  conversationId: string;
  createdNew: boolean;
  createdFolderId: string | null;
  draft: {
    value: string;
    attachments: PendingAttachment[];
    agentMentions: PendingAgentMention[];
  };
  navigate: (to: string) => void;
  supportPack?: SupportDiagnosticIds;
}): Promise<void> {
  const sendError = composerSendErrorFromRuntime(conversationId);
  const error = sendError
    ? { ...sendError, ...(supportPack ? { supportPack } : {}) }
    : null;
  const shouldTeardown =
    createdNew && getRuntime(conversationId).messages.length === 0;

  if (shouldTeardown) {
    try {
      await deleteConversation(conversationId);
    } catch {
      restoreComposerDraft(conversationId, draft);
      if (error) setComposerSendError(conversationId, error);
      return;
    }
    applyDeletedConversationLocally(conversationId);
    const draftKey = draftKeyFor(null);
    forgetDraftRequestId(draftKey);
    restoreComposerDraft(null, {
      ...draft,
      attachments: attachmentsForUnstartedRetry(draft.attachments),
    });
    if (error) setComposerSendError(draftKey, error);
    if (createdFolderId) {
      useFoldersStore.getState().setDraftWorkspaceIntent({
        kind: "folder",
        folderId: createdFolderId,
      });
    }
    useConversationStore.getState().switchConversation(null);
    navigate("/");
    return;
  }

  restoreComposerDraft(conversationId, draft);
  if (error) setComposerSendError(conversationId, error);
}

export function useComposerSend({
  value,
  setValue,
  attachments,
  setAttachments,
  agentMentions,
  setAgentMentions,
  isGenerating,
  backgroundMode,
  isLocal,
  closeMenu,
}: {
  value: string;
  setValue: Dispatch<SetStateAction<string>>;
  attachments: PendingAttachment[];
  setAttachments: Dispatch<SetStateAction<PendingAttachment[]>>;
  agentMentions: PendingAgentMention[];
  setAgentMentions: Dispatch<SetStateAction<PendingAgentMention[]>>;
  isGenerating: boolean;
  backgroundMode: boolean;
  isLocal: boolean;
  closeMenu: () => void;
}) {
  const addMessage = useConversationStore((s) => s.addMessage);
  const navigate = useNavigate();
  // 门闩 + 按钮 in-flight 态存在 store 里、与草稿同键：组件重挂载（切对话回来、居中
  // 草稿 → 底栏、路由重建、刷新 / 重启）不再把闸归零，也不再丢「在发」的视觉态。
  const activeConversationId = useConversationStore(
    (s) => s.currentConversationId,
  );
  const sendPhase = useComposerSendPhase(draftKeyFor(activeConversationId));
  const isSending = sendPhase !== null;

  const toOutgoingMentions = useCallback(
    (pending: PendingAgentMention[]): OutgoingAgentMention[] =>
      pending.slice(0, MAX_AGENT_MENTIONS).map((a) => ({
        agent_id: a.agentId,
        role: a.role,
      })),
    [],
  );

  const clearComposer = useCallback(() => {
    setValue("");
    setAttachments([]);
    setAgentMentions([]);
    closeMenu();
  }, [setValue, setAttachments, setAgentMentions, closeMenu]);

  /** 摘掉发不出去的 chip（主进程暂存已被清）并注销它们的在途上传。 */
  const dropStaleAttachments = useCallback(
    (staleIds: readonly string[]) => {
      if (staleIds.length === 0) return;
      for (const id of staleIds) forgetAttachmentUpload(id);
      setAttachments((prev) => prev.filter((x) => !staleIds.includes(x.id)));
    },
    [setAttachments],
  );

  // biome-ignore lint/correctness/useExhaustiveDependencies: closeMenu/setValue/setAgentMentions kept for stable identity when clearComposer path is not taken
  const handleSend = useCallback(
    async (opts?: { delivery?: MessageDelivery }) => {
      const trimmed = value.trim();
      if (!trimmed && attachments.length === 0) return;

      // N4-A：只读离线硬禁用（按钮已 disabled；此处兜底防键盘/程序化触发）。
      if (isReadOnlyOffline()) {
        notifyError("离线时无法发送，请恢复连接后再试");
        return;
      }

      const activeConvId =
        useConversationStore.getState().currentConversationId;
      const draftKey = draftKeyFor(activeConvId);

      // 挂起弱提示：有待确认卡时先二次确认（同会话确认一次后不再弹）；正规续跑/
      // 提交卡不受影响。生成中再发走 mid-flight，不套本确认。
      if (!confirmSendDespitePendingIfNeeded(activeConvId, isGenerating)) {
        return;
      }

      const delivery: MessageDelivery =
        opts?.delivery ?? resolveDefaultDelivery(isGenerating, activeConvId);

      const outgoingMentions = toOutgoingMentions(agentMentions);

      // Mid-flight：生成中发送走独立 POST SSE（steer 插话 / queue 排队）。
      // queue：ack 后清 composer；条进 QueuedTurnsBar，出队开跑再插用户泡。
      // steer（经典/协调）不经 addMessage——主时间线由 InterjectionTimeline 投影
      // execution.userInterjections（user_interjection SSE · DURABLE）。
      if (isGenerating && activeConvId) {
        if (!acquireComposerSendLatch(draftKey, "sending")) return;
        clearComposerSendError(draftKey);
        try {
          const pending = attachments;
          const settled = await settleAttachments(
            activeConvId,
            pending,
            "midflight",
          );
          if (!settled.ok) {
            // 原始错误优先：后端 ApiError 带着 code / serverMessage，包成
            // `new Error(reason)` 就只剩通用兜底文案了。
            notifyError(settled.cause ?? settled.reason, "附件驻留失败");
            dropStaleAttachments(settled.staleIds);
            return;
          }

          const result = await sendMidFlightMessage(
            activeConvId,
            trimmed,
            settled.outgoing.length > 0 ? settled.outgoing : undefined,
            delivery,
            outgoingMentions.length > 0 ? outgoingMentions : undefined,
          );
          if (result.kind === "received" || result.kind === "queued") {
            for (const a of pending) forgetAttachmentUpload(a.id);
            clearComposer();
            // queued：条由 turn_queued → midFlight upsert；出队再插泡。
            // received：主时间线走 user_interjection SSE 投影（含经典 durable 气泡）。
          }
        } finally {
          releaseComposerSendLatch(draftKey);
        }
        return;
      }

      if (isGenerating) return;

      if (backgroundMode && isLocal && activeConvId) {
        clearComposerSendError(draftKey);
        dispatchBackgroundTask(activeConvId, trimmed);
        // 后台任务只带正文；随草稿一起丢掉的附件也要注销，别让在途上传攥着 File。
        for (const a of attachments) forgetAttachmentUpload(a.id);
        clearComposer();
        return;
      }

      // 草稿首发先闩「建会话中」：这段等待里输入框已经清空，进行中态见发送键 in-flight。
      const initialPhase = activeConvId ? "sending" : "creating";
      if (!acquireComposerSendLatch(draftKey, initialPhase)) return;
      clearComposerSendError(draftKey);
      // 门闩键随 promote 迁移（`__draft__` → 新会话 id），故不是常量。
      let latchKey = draftKey;
      // 只放一次：回合上路后就提前放闸（见下），此后 finally 不能再放——那时闸可能
      // 已经属于下一次发送，替它放掉就等于把防连点闸打开。
      let latchHeld = true;
      const releaseLatch = () => {
        if (!latchHeld) return;
        latchHeld = false;
        releaseComposerSendLatch(latchKey);
      };
      try {
        const pending = attachments;
        const store = useConversationStore.getState();
        const isFirstMessage = getActiveRuntime().messages.length === 0;

        let conversationId = store.currentConversationId;
        let createdNew = false;
        let createdFolderId: string | null = null;
        if (!conversationId) {
          const intent = useFoldersStore.getState().draftWorkspaceIntent;
          const targetFolderId =
            intent.kind === "folder" ? intent.folderId : null;
          // Project chats inherit workspace — never write session-level local_*.
          // Quick cloud (default) → null container.
          // §7.2：残留 quick_local 意图硬改导云（禁新建本机草稿）；存量会话不经此分支。
          const localContainerRootId: string | null = null;
          if (intent.kind === "quick_local") {
            useFoldersStore
              .getState()
              .setDraftWorkspaceIntent({ kind: "quick_cloud" });
          }
          // 新建拍快照：POST 带 last-used（或草稿所选，已写入 last_profile_id）；
          // 省略则服务端写入当时账号默认。勿再 create 后 PATCH。
          const inheritedProfileId = getLastUsedProfileId()?.trim() || null;
          // 幂等键跟草稿走：同一份草稿重复发送复用同一个键，服务端命中同键返回已建好的
          // 那条会话，「以为没发出去又按一次」不会再建出第二条。
          const clientRequestId = resolveDraftRequestId(draftKey);
          // 清空输入框排在创建 POST 之前：按下发送到 POST 返回之间界面若毫无变化（文字
          // 还在、没气泡、没跳转），用户就会再按一次——线上重复建会话的 3.7s / 5.6s 两例
          // 正是这个形状。等待期间的进行中态见发送键 in-flight；创建失败时下面把
          // 整份草稿（正文 + 附件 + 点名）原样还回。
          clearComposer();
          try {
            const permissionAxes = await resolveDefaultPermissionAxes();
            const conv = await api.post<{
              id: string;
              permission_axes?: PermissionAxes;
              model_profile_id?: string | null;
            }>("/v1/conversations", {
              title: null,
              folder_id: targetFolderId,
              local_container_root_id: localContainerRootId,
              permission_axes: permissionAxes,
              client_request_id: clientRequestId,
              ...(inheritedProfileId
                ? { model_profile_id: inheritedProfileId }
                : {}),
            });
            conversationId = conv.id;
            setComposerDraftAxes(null);
            upsertConversationFront({
              id: conv.id,
              title: provisionalConversationTitle(trimmed),
              updatedAt: new Date().toISOString(),
              messageCount: 0,
              lastMessagePreview: null,
              folderId: targetFolderId,
              localContainerRootId,
              permissionAxes: conv.permission_axes ?? permissionAxes,
              modelProfileId:
                conv.model_profile_id ?? inheritedProfileId ?? null,
            });
            // 首发落地动画：仅在草稿 promote 成新对话时武装 dock-flip（中间→底栏）。切换到
            // 已有对话不走这里，故不会误触发动画——这正是修掉「输入框跳动」的关键。必须在
            // switchConversation 前武装，让 conversationId 翻转的那一帧就带上信号；
            // navigate 也必须留在两者之后（见下），三者的先后顺序不要改。
            useComposerDraftStore.getState().armDockFlip();
            useConversationStore.getState().switchConversation(conv.id);
            createdNew = true;
            createdFolderId = targetFolderId;
            useFoldersStore.getState().resetDraftWorkspaceIntent();
            // 门闩跟着 draftKey 迁移，别在翻转的那一帧漏出一个可点的发送键。
            latchKey = draftKeyFor(conv.id);
            moveComposerSendLatch(draftKey, latchKey, "sending");
          } catch (err) {
            // 建会话失败：仍是草稿态，把先前清掉的草稿原样还给用户（参照下面附件驻留
            // 失败的回滚），并把这次的幂等键钉回草稿——重试要复用它才命中服务端幂等。
            restoreComposerDraft(null, {
              value: trimmed,
              attachments: pending,
              agentMentions,
            });
            pinDraftRequestId(draftKey, clientRequestId);
            notifyError(err, "新建对话失败");
            return;
          }
        }

        if (!isFirstMessage && getActiveRuntime().hasMoreAfter) {
          try {
            await loadLatestWindow(conversationId);
          } catch {
            /* best-effort */
          }
        }

        // 乐观气泡与清空输入框排在等待附件之前：附件在附加时就开始上传了，点发送
        // 只是等它收尾——没有理由让用户对着还留着文字和 chip 的输入框干等。
        const userMsgId = crypto.randomUUID();
        addMessage({
          id: userMsgId,
          role: "user",
          content: trimmed,
          createdAt: new Date().toISOString(),
          executionId: null,
          isStreaming: false,
          attachments: pending.length
            ? pending.map((a) => ({
                id: a.id,
                name: a.name,
                path: a.path,
                truncated: a.truncated,
                kind: a.kind,
                conversationId: a.conversationId,
                workspacePath: a.workspacePath,
              }))
            : undefined,
          agentMentions: agentMentions.length
            ? agentMentions.map((a) => ({
                agentId: a.agentId,
                role: a.role,
              }))
            : undefined,
        });
        clearComposer();

        if (isFirstMessage) {
          patchConversationCache(conversationId, {
            title: provisionalConversationTitle(trimmed),
          });
          // Local sidecar has no cloud SSE title_generated — mint in parallel with
          // the turn (same core as cloud schedule_title_generation).
          scheduleLocalAutoTitle(conversationId, trimmed);
        }

        if (createdNew) {
          navigate(`/conversations/${conversationId}`);
        }

        const settled = await settleAttachments(
          conversationId,
          pending,
          "send",
        );
        if (!settled.ok) {
          // 不留假气泡：撤掉乐观气泡，把草稿（正文 + 附件 + 点名）还给用户重试，
          // 只摘掉主进程暂存已失效、留着也发不出去的那几条。
          useConversationStore
            .getState()
            .removeMessage(userMsgId, conversationId);
          restoreComposerDraft(conversationId, {
            value: trimmed,
            attachments: pending.filter(
              (a) => !settled.staleIds.includes(a.id),
            ),
            agentMentions,
          });
          for (const id of settled.staleIds) forgetAttachmentUpload(id);
          notifyError(settled.cause ?? settled.reason, "附件驻留失败");
          return;
        }

        if (pending.length > 0) {
          // 驻留落地后才知道真实 ``attachments/…`` 路径：补正乐观气泡（下载链接靠它）。
          useConversationStore.getState().updateMessage(
            userMsgId,
            {
              attachments: pending.map((a, i) => ({
                id: a.id,
                name: settled.outgoing[i]?.name ?? a.name,
                path: settled.outgoing[i]?.path ?? a.path,
                truncated: a.truncated,
                kind: a.kind,
                conversationId: a.conversationId,
                workspacePath: settled.outgoing[i]?.workspace_path,
              })),
            },
            conversationId,
          );
        }

        // in-flight 态只覆盖「点击 → 回合已上路」这一段。回合本身的流式由生成态接管，
        // 否则整轮生成期间「排队 / 插队」都会被误锁。
        releaseLatch();

        try {
          const sent = await sendTurn({
            conversationId,
            content: trimmed,
            attachments: settled.outgoing,
            agentMentions: outgoingMentions,
            optimisticUserId: userMsgId,
            delivery: "steer",
          });
          if (sent?.unstartedRefusal && conversationId) {
            // 先还芯片（要读 recover blob），再忘掉登记。
            await restoreAfterUnstartedRefusal({
              conversationId,
              createdNew,
              createdFolderId,
              draft: {
                value: trimmed,
                attachments: pending,
                agentMentions,
              },
              navigate,
              supportPack: sent.supportPack,
            });
          }
        } finally {
          for (const a of pending) forgetAttachmentUpload(a.id);
        }
      } finally {
        releaseLatch();
      }
    },
    [
      value,
      attachments,
      agentMentions,
      isGenerating,
      addMessage,
      navigate,
      closeMenu,
      backgroundMode,
      isLocal,
      setValue,
      setAttachments,
      setAgentMentions,
      toOutgoingMentions,
      clearComposer,
      dropStaleAttachments,
    ],
  );

  return {
    handleSend,
    isSending,
    isCreatingConversation: sendPhase === "creating",
  };
}
