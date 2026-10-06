import { DraftWorkspaceAssignPrompt } from "@/components/chat/DraftWorkspaceAssignPrompt";
import { MentionMenu } from "@/components/chat/MentionMenu";
import { IconButton } from "@/components/ui";
import { useConversations } from "@/hooks/useConversations";
import { useFolders } from "@/hooks/useFolders";
import { useChatPaneId, useChatPaneSliceKey } from "@/lib/chatPane";
import {
  COMPOSER_CONTINUE_PLACEHOLDER,
  isContinuableAssistant,
} from "@/lib/composerContinueHint";
import {
  resolveOccupiedShortcutDelivery,
  useClassicToolStepOpen,
  useLiveCoordinatingTurn,
} from "@/lib/composerDelivery";
import { connectivityEscalationSuffix } from "@/lib/errors";
import {
  dropInlineIndex,
  insertInlineToken,
  migrateLegacyDraft,
  plainText,
} from "@/lib/inlineBody";
import {
  assistantHasTeamStrip,
  turnOutcomeForAssistant,
} from "@/lib/turnOutcome";
import { selectVisibleColdResumes } from "@/services/resume";
import { warmLlmHttpOnComposerFocus } from "@/services/warmLlmHttp";
import { draftKeyFor, useComposerDraftStore } from "@/stores/composer";
import {
  clearFailureBannerDismiss,
  dismissFailureBanner,
  useFailureBannerDismissed,
} from "@/stores/composerFailureDismiss";
import {
  clearComposerSendError,
  useComposerSendError,
} from "@/stores/composerSendError";
import {
  assistantProjectionId,
  runtimeOf,
  useActiveError,
  useActiveErrorAction,
  useActiveGenerating,
  useActiveTurnPhase,
  useConversationStore,
} from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { useFoldersStore } from "@/stores/folders";
import {
  useInteractionStore,
  usePendingApprovals,
} from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import { useServerHealthStore } from "@/stores/serverHealth";
import { AtSign, Loader2, Send, Square, X } from "lucide-react";
import type { ChangeEvent, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AssemblyPicker } from "./AssemblyPicker";
import {
  ComposerBodyEditor,
  type ComposerBodyHandle,
} from "./ComposerBodyEditor";
import { ComposerContextCompactedHint } from "./ComposerContextCompactedHint";
import { ComposerFailureBanner } from "./ComposerFailureBanner";
import { ComposerGitStatusChip } from "./ComposerGitStatusChip";
import {
  ComposerPlusMenu,
  useComposerPlusClose,
  useComposerPlusHost,
} from "./ComposerPlusMenu";
import { ComposerReceivedContextButton } from "./ComposerReceivedContextButton";
import { ComposerVisionHint } from "./ComposerVisionHint";
import { ComposerWorkspaceChip } from "./ComposerWorkspaceChip";
import { PermissionAxesBadge } from "./PermissionPresetBadge";
import { RecordingBar } from "./RecordingBar";
import { ComposerConnectionNotice } from "./ServerStatusIndicator";
import { VoiceButton } from "./VoiceButton";
import { forgetAttachmentUpload } from "./attachmentUploads";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "./composerAttachments";
import { composerHasSendableDraft } from "./composerAttachments";
import {
  COMPOSER_FOLDER_READ_ONLY_HINT,
  isComposerFolderWriteBlocked,
} from "./composerFolderWrite";
import {
  COMPOSER_DEBATE_STEER_PLACEHOLDER,
  useLiveDebateSteer,
} from "./liveDebateSteer";
import { decideDraftFolderAssign } from "./resolveAttachmentFolder";
import { useComposerDrop } from "./useComposerDrop";
import { useComposerSend } from "./useComposerSend";
import type { AttachmentFolderHint } from "./useMentionMenu";
import { useMentionMenu } from "./useMentionMenu";
import { useVoiceInput } from "./useVoiceInput";

const EMPTY_ATTACHMENTS: PendingAttachment[] = [];
const EMPTY_AGENT_MENTIONS: PendingAgentMention[] = [];
/** Zustand getSnapshot must return a cached empty — a fresh `[]` loops React. */
const EMPTY_MESSAGES: { id: string; role: string }[] = [];

// 输入框自增高边界：card 空/单行草稿保底 ~2 行（text-sm 20px 行高 + pt-3/pb-1 = 56px）；
// bar 默认一行高（20px 行高 + py-2 = 36px）。上限 200px 后转内部滚动。
const MIN_COMPOSER_HEIGHT_CARD = 56;
const MIN_COMPOSER_HEIGHT_BAR = 36;
const MAX_COMPOSER_HEIGHT = 200;

/** bar 底排盒：＋ / 输入 / 语音 / 发送。窗外环用同一 `py-1` 对齐，不跟卡片边框底边对齐。 */
const COMPOSER_BAR_ROW = "flex items-end gap-1 py-1";
const COMPOSER_BAR_CLUSTER = "flex shrink-0 items-center pb-0.5";
/** card 底栏：窗外环与工具条同一 `pb-3`，不跟卡片边框底边对齐。 */
const COMPOSER_CARD_ENDCAP = "flex items-end pb-3";
/**
 * 窗口环不进输入列的 flex。默认整颗挂在卡片右侧空白（`left: 100% + 4px`）。
 * 聊天列窄于 50.5rem 时，globals.css `@container chat` 把环收回列内留白，
 * 并给 `[data-composer-actions]` 加 end padding，避免压住发送或被裁切。
 */
const COMPOSER_ENDCAP_PLACE =
  "absolute bottom-0 z-10 left-[calc(100%+0.25rem)]";

/** Align with backend `MessageCreate.content` max_length. */
const MESSAGE_CHAR_LIMIT = 32_000;

export type TurnComposerVariant = "card" | "bar";

/**
 * The ONE turn composer (统一 AI 输入框): the full-featured card — auto-growing
 * textarea, @ 引用（含本机附件）, drag-drop attachments, 停止生成,
 * 回填 channel — hosted by the chat view's
 * {@link import("../MessageInput").MessageInput}. Canvas is look-only; 下达指令
 * stays in chat. Hosts only pick chrome (placeholder).
 *
 * `variant="bar"` is the compact single-row chrome used only by the chat bottom dock:
 * `[＋]` · textarea · 语音 · 发送；工作区/Git/模型/权限/@ 收进＋菜单。
 * 窗口环贴在整块输入框外侧右边，不进卡片、不占输入列宽；与底排同一行盒（不是贴边框底边）。
 * default `card` keeps textarea-above-toolbar（居中草稿），左簇摊开。
 * 离线态靠 {@link ComposerConnectionNotice} 与发送硬禁，不再用安静连接绿点。
 *
 * Draft state (text + attachments) lives in {@link useComposerDraftStore} keyed by
 * conversation, NOT in component state — remounts (切对话回来、居中草稿 → 底栏、
 * 刷新 / 重启) keep the half-typed order, and 回填 (ask card / run-detail / debate)
 * lands in the draft even if the composer is briefly unmounted. The textarea stays typable
 * while a turn is generating (queue up the next order); only sending is gated, with
 * 停止 in the send slot.
 *
 * Draft-conversation-only concerns (workspace picker, attachment→folder hint) are
 * self-gated on `!conversationId`.
 */
export function TurnComposer({
  placeholder = "输入消息，@ 引用内容…",
  variant = "card",
  attachedBelowApproval = false,
}: {
  placeholder?: string;
  /**
   * `card` = editor above toolbar (default; centered new-chat composer).
   * `bar` = compact dock: ＋菜单收纳左簇，常显仅输入与发送。
   */
  variant?: TurnComposerVariant;
  /** Visually fuse with ApprovalPrompt stacked above (工具审批 A · Composer 一体). */
  attachedBelowApproval?: boolean;
}) {
  const isBar = variant === "bar";
  const minComposerHeight = isBar
    ? MIN_COMPOSER_HEIGHT_BAR
    : MIN_COMPOSER_HEIGHT_CARD;
  const isGenerating = useActiveGenerating();
  const teamLive = useLiveCoordinatingTurn();
  const toolStepOpen = useClassicToolStepOpen();
  const liveDebate = useLiveDebateSteer();
  const turnPhase = useActiveTurnPhase();
  const isStopping = turnPhase === "stopping";
  // 冻图会立刻关 isGenerating、execution 也不再算活队；停完之前输入框仍走停止，不露出发送。
  const deskOccupied = isGenerating || teamLive || isStopping;
  const conversationId = useChatPaneId();
  const paneKey = useChatPaneSliceKey();
  const byId = useInteractionStore((s) => s.byId);
  const pausedPending = usePausedTurnStore((s) => s.pending);
  const recoveryState = usePausedTurnStore((s) =>
    conversationId
      ? (s.openRecovery?.[conversationId] ?? "unresolved")
      : "unresolved",
  );
  const hasVisibleColdResume = useConversationStore((s) => {
    if (!conversationId) return false;
    return (
      selectVisibleColdResumes({
        conversationId,
        byId,
        pausedPending,
        messages: s.byId?.[paneKey]?.messages ?? EMPTY_MESSAGES,
        recoveryState,
      }).length > 0
    );
  });
  const pendingApprovals = usePendingApprovals(conversationId);
  const lastMessageRaw = useConversationStore((s) => {
    const rt = runtimeOf(s, paneKey);
    if (rt.isGenerating) return null;
    return rt.messages.at(-1) ?? null;
  });
  const lastMessage = deskOccupied ? null : lastMessageRaw;
  const showPendingHint =
    !!conversationId &&
    !deskOccupied &&
    (hasVisibleColdResume || pendingApprovals.length > 0);
  const lastSlot = useExecutionStore((s) => {
    if (!lastMessage || lastMessage.role !== "assistant") return undefined;
    return s.byId[assistantProjectionId(lastMessage)];
  });
  const sessionError = useActiveError();
  const lastOutcome =
    lastMessage?.role === "assistant"
      ? turnOutcomeForAssistant(lastMessage, lastSlot, {
          hasPendingDecision: showPendingHint,
          conversationError: sessionError,
          hasTeamStrip: assistantHasTeamStrip(lastMessage, lastSlot),
        })
      : null;
  const showComposerHint =
    !deskOccupied && Boolean(lastOutcome?.showComposerHint);
  const turnMessageId =
    lastMessage?.role === "assistant"
      ? assistantProjectionId(lastMessage)
      : null;
  const bannerDismissed = useFailureBannerDismissed(
    conversationId,
    turnMessageId,
  );
  // biome-ignore lint/correctness/useExhaustiveDependencies: conversationId is the reset key — clear the banner dismiss when the chat changes.
  useEffect(() => {
    return () => clearFailureBannerDismiss();
  }, [conversationId]);
  const serverStatus = useServerHealthStore((s) => s.status);
  const serverUnhealthy = serverStatus === "offline";
  const resolvedPlaceholder = useMemo(() => {
    if (liveDebate) return COMPOSER_DEBATE_STEER_PLACEHOLDER;
    if (!deskOccupied && isContinuableAssistant(lastMessage)) {
      return COMPOSER_CONTINUE_PLACEHOLDER;
    }
    return placeholder;
  }, [liveDebate, deskOccupied, lastMessage, placeholder]);
  const draftKey = draftKeyFor(conversationId);
  const composerError = useComposerSendError(draftKey);
  const sessionAction = useActiveErrorAction();
  const turnBannerMessage = (() => {
    if (
      !showComposerHint ||
      !lastOutcome?.message ||
      bannerDismissed ||
      lastMessage?.role !== "assistant"
    ) {
      return null;
    }
    if (lastOutcome.kind !== "error") return lastOutcome.message;
    const suffix = connectivityEscalationSuffix(
      lastMessage.error?.code ?? lastOutcome.code ?? undefined,
      turnMessageId ?? lastMessage.id,
      {
        message: lastMessage.error?.message,
        upstreamStatus: lastMessage.error?.context?.upstream_status,
        emptyDiagnosis: lastMessage.error?.context?.empty_diagnosis,
        conversationId,
      },
    );
    return suffix ? `${lastOutcome.message}${suffix}` : lastOutcome.message;
  })();
  const suppressSession = Boolean(
    lastOutcome && !lastOutcome.showSessionBanner,
  );
  const turnAction =
    lastOutcome?.recovery.kind === "configure" &&
    lastOutcome.recovery.href &&
    lastOutcome.recovery.label
      ? {
          label: lastOutcome.recovery.label,
          href: lastOutcome.recovery.href,
        }
      : null;
  const failureNotice = composerError?.message
    ? {
        message: composerError.message,
        action: composerError.action,
        onDismiss: () => {
          clearComposerSendError(draftKey);
          useConversationStore.getState().clearError();
        },
      }
    : showComposerHint && turnBannerMessage && turnMessageId
      ? {
          message: turnBannerMessage,
          action: turnAction,
          onDismiss: () => dismissFailureBanner(conversationId, turnMessageId),
        }
      : !suppressSession && sessionError
        ? {
            message: sessionError,
            action: sessionAction,
            onDismiss: () => {
              useConversationStore.getState().clearError();
            },
          }
        : null;
  const value = useComposerDraftStore((s) => s.drafts[draftKey]?.value ?? "");
  const attachments = useComposerDraftStore(
    (s) => s.drafts[draftKey]?.attachments ?? EMPTY_ATTACHMENTS,
  );
  const agentMentions = useComposerDraftStore(
    (s) => s.drafts[draftKey]?.agentMentions ?? EMPTY_AGENT_MENTIONS,
  );
  const setValue = useCallback(
    (action: SetStateAction<string>) =>
      useComposerDraftStore.getState().setValue(draftKey, action),
    [draftKey],
  );
  const setAttachments = useCallback(
    (action: SetStateAction<PendingAttachment[]>) =>
      useComposerDraftStore.getState().setAttachments(draftKey, action),
    [draftKey],
  );
  const setAgentMentions = useCallback(
    (action: SetStateAction<PendingAgentMention[]>) =>
      useComposerDraftStore.getState().setAgentMentions(draftKey, action),
    [draftKey],
  );

  const bodyRef = useRef<ComposerBodyHandle | null>(null);
  const bodyHostRef = useRef<HTMLDivElement>(null);
  const pendingCaretRef = useRef<number | null>(null);
  const folders = useFolders();
  const conversations = useConversations();
  const draftIntent = useFoldersStore((s) => s.draftWorkspaceIntent);
  const folderReadOnly = isComposerFolderWriteBlocked({
    conversationId,
    conversations,
    folders,
    draftIntent,
  });
  const sendBlocked = serverUnhealthy || folderReadOnly;
  const sendBlockedTitle = serverUnhealthy
    ? "离线时无法发送"
    : folderReadOnly
      ? COMPOSER_FOLDER_READ_ONLY_HINT
      : undefined;
  const pendingFolderId =
    draftIntent.kind === "folder" ? draftIntent.folderId : null;
  const dismissedAssignRef = useRef<Set<string>>(new Set());
  const [assignHint, setAssignHint] = useState<AttachmentFolderHint | null>(
    null,
  );

  const handleAttachmentFolderHint = useCallback(
    (hint: AttachmentFolderHint) => {
      const store = useFoldersStore.getState();
      const decision = decideDraftFolderAssign(
        hint,
        store.draftWorkspaceIntent,
      );
      if (decision.action === "none") return;
      if (decision.action === "auto") {
        store.setDraftWorkspaceIntent({
          kind: "folder",
          folderId: decision.folderId,
        });
        return;
      }
      if (dismissedAssignRef.current.has(hint.folderId)) return;
      setAssignHint(hint);
    },
    [],
  );

  const fileInputRef = useRef<HTMLInputElement>(null);
  const onBrowserFilePick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const insertTokenAtCaret = useCallback(
    (kind: "A" | "M", index: number) => {
      setValue((prev) => {
        const caret =
          pendingCaretRef.current ?? bodyRef.current?.getCaret() ?? prev.length;
        const ins = insertInlineToken(prev, caret, kind, index);
        pendingCaretRef.current = ins.caret;
        return ins.value;
      });
      requestAnimationFrame(() => {
        const caret = pendingCaretRef.current;
        if (caret == null) return;
        bodyRef.current?.focus();
        bodyRef.current?.setCaret(caret);
        pendingCaretRef.current = null;
      });
    },
    [setValue],
  );

  const mention = useMentionMenu({
    conversationId,
    value,
    setValue,
    attachments,
    setAttachments,
    agentMentions,
    setAgentMentions,
    bodyRef,
    onAttachmentFolderHint: conversationId
      ? undefined
      : handleAttachmentFolderHint,
    onBrowserFilePick,
  });

  const onAttachmentInserted = useCallback(
    (index: number) => {
      insertTokenAtCaret("A", index);
    },
    [insertTokenAtCaret],
  );

  const drop = useComposerDrop(
    attachments,
    setAttachments,
    conversationId,
    onAttachmentInserted,
    conversationId ? undefined : handleAttachmentFolderHint,
  );

  const onBrowserFilesSelected = useCallback(
    async (e: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      e.target.value = "";
      if (files.length === 0) return;
      mention.clearActiveMention();
      await drop.attachFiles(files);
    },
    [drop.attachFiles, mention.clearActiveMention],
  );

  const voice = useVoiceInput({
    onTranscript: useCallback(
      (text: string) => {
        setValue((prev) => {
          const caret =
            pendingCaretRef.current ??
            bodyRef.current?.getCaret() ??
            prev.length;
          pendingCaretRef.current = caret + text.length;
          return prev.slice(0, caret) + text + prev.slice(caret);
        });
        requestAnimationFrame(() => {
          const caret = pendingCaretRef.current;
          if (caret == null) return;
          bodyRef.current?.focus();
          bodyRef.current?.setCaret(caret);
          pendingCaretRef.current = null;
        });
      },
      [setValue],
    ),
  });

  const { handleSend, isSending } = useComposerSend({
    value,
    setValue,
    attachments,
    setAttachments,
    agentMentions,
    setAgentMentions,
    isGenerating: deskOccupied,
    backgroundMode: false,
    isLocal: false,
    closeMenu: mention.closeMenu,
  });

  const adjustHeight = useCallback(() => {
    const el = bodyHostRef.current?.querySelector<HTMLElement>(
      "[data-testid=composer-body]",
    );
    if (!el) return;
    el.style.height = "0";
    el.style.height = `${Math.min(
      Math.max(el.scrollHeight, minComposerHeight),
      MAX_COMPOSER_HEIGHT,
    )}px`;
  }, [minComposerHeight]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: value is an intentional re-run key
  useEffect(() => {
    adjustHeight();
  }, [value, adjustHeight]);

  // 回填 focus hint: the fill's text arrives through the store subscription; the token
  // only asks the mounted composer to refocus. Seeding the ref with the current token
  // makes a remount (view switch / navigation) ignore fills that happened before it.
  const fillToken = useComposerDraftStore((s) => s.fillToken);
  const seenFillRef = useRef(fillToken);
  useEffect(() => {
    if (fillToken === seenFillRef.current) return;
    seenFillRef.current = fillToken;
    requestAnimationFrame(() => bodyRef.current?.focus());
  }, [fillToken]);

  useEffect(() => {
    const current = useComposerDraftStore.getState().drafts[draftKey];
    if (!current) return;
    const migrated = migrateLegacyDraft(
      current.value,
      current.attachments.length,
      (current.agentMentions ?? []).length,
    );
    if (migrated !== current.value) {
      useComposerDraftStore.getState().setValue(draftKey, migrated);
    }
  }, [draftKey]);

  useEffect(() => {
    if (!conversationId) {
      dismissedAssignRef.current = new Set();
    }
    setAssignHint(null);
  }, [conversationId]);

  useEffect(() => {
    if (assignHint && pendingFolderId === assignHint.folderId) {
      setAssignHint(null);
    }
  }, [assignHint, pendingFolderId]);

  const currentFolderName = pendingFolderId
    ? (folders.find((f) => f.id === pendingFolderId)?.name ?? null)
    : null;

  const acceptAssignHint = useCallback(() => {
    if (!assignHint) return;
    useFoldersStore.getState().setDraftWorkspaceIntent({
      kind: "folder",
      folderId: assignHint.folderId,
    });
    setAssignHint(null);
  }, [assignHint]);

  const dismissAssignHint = useCallback(() => {
    if (!assignHint) return;
    dismissedAssignRef.current.add(assignHint.folderId);
    setAssignHint(null);
  }, [assignHint]);

  const handleBodyChange = useCallback(
    (next: string) => {
      setValue(next);
      if (!liveDebate) {
        mention.syncMention(next, bodyRef.current?.getCaret() ?? next.length);
      }
      if (drop.dropError) drop.clearDropError();
    },
    [drop, liveDebate, mention, setValue],
  );

  const handleCaret = useCallback(
    (caret: number) => {
      if (liveDebate) return;
      mention.syncMention(value, caret);
    },
    [liveDebate, mention, value],
  );

  const handleReconcile = useCallback(
    (nextAtts: PendingAttachment[], nextMents: PendingAgentMention[]) => {
      const removed = attachments.filter(
        (a) => !nextAtts.some((b) => b.id === a.id),
      );
      for (const a of removed) forgetAttachmentUpload(a.id);
      setAttachments(nextAtts);
      setAgentMentions(nextMents);
    },
    [attachments, setAttachments, setAgentMentions],
  );

  const removeAttachment = useCallback(
    (id: string) => {
      const index = attachments.findIndex((a) => a.id === id);
      forgetAttachmentUpload(id);
      if (index >= 0) {
        setValue((prev) => dropInlineIndex(prev, "attachment", index));
      }
      setAttachments((prev) => prev.filter((a) => a.id !== id));
    },
    [attachments, setAttachments, setValue],
  );

  const removeAgentMention = useCallback(
    (id: string) => {
      const index = agentMentions.findIndex((a) => a.id === id);
      if (index >= 0) {
        setValue((prev) => dropInlineIndex(prev, "mention", index));
      }
      setAgentMentions((prev) => prev.filter((a) => a.id !== id));
    },
    [agentMentions, setAgentMentions, setValue],
  );

  const stopGeneration = useCallback(() => {
    useConversationStore.getState().stopGeneration();
  }, []);

  useEffect(() => {
    if (voice.isRecording) mention.closeMenu();
  }, [voice.isRecording, mention.closeMenu]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (
        !(e.ctrlKey || e.metaKey) ||
        !e.shiftKey ||
        e.key.toLowerCase() !== "v"
      )
        return;
      if (!voice.isSupported) return;
      e.preventDefault();
      voice.toggle();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [voice.isSupported, voice.toggle]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return;

    if (voice.isRecording) {
      if (e.key === "Escape") {
        e.preventDefault();
        voice.cancel();
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        voice.stop();
        return;
      }
    }

    if (!liveDebate && mention.menuMode && mention.handleMenuNavKey(e)) return;

    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      // 辩论进行中一律 continue。
      // 生成中：队还在或工具步还在执行才 steer；散文与 Enter 相同，排队。
      // 空闲与 Enter 同路径（默认 steer），勿伪装传 queue。
      if (sendBlocked) return;
      if (liveDebate) {
        void handleSend();
      } else if (deskOccupied) {
        void handleSend({
          delivery: resolveOccupiedShortcutDelivery(conversationId),
        });
      } else {
        void handleSend();
      }
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      // 离线 / 只读协作桌硬禁用（与发送按钮一致；handleSend 仍有兜底）。
      if (sendBlocked) return;
      // 辩论进行中 continue。其余不传 delivery：空闲默认 steer；生成中默认 queue。
      void handleSend();
    }
  };

  const menuOpen = mention.menuMode !== null;

  // 左簇顺序：工作区 · Git? · 装配 · 权限 · @
  // bar：整簇收进 ComposerPlusMenu（权限/@ 带文案）；card：底栏摊开（权限 iconOnly）。
  // 否决 Composer 并排「本地引擎/云端过桥」切换器；引擎不可用走诊断横幅，不自动过桥。
  const sessionChrome = (
    <>
      <ComposerWorkspaceChip conversationId={conversationId} />
      <ComposerGitStatusChip conversationId={conversationId} />
      <AssemblyPicker disabled={deskOccupied} />
      <PermissionAxesBadge disabled={deskOccupied} iconOnly={!isBar} />
    </>
  );

  // 生成中照常可开 @：插话 / 排队本就带附件走，禁用只会让人以为坏了。
  // 辩论进行中不画：主框是对这场说话，不是定向掌舵。
  const mentionButton = (
    <ComposerMentionButton
      onToggle={mention.toggleAtMention}
      iconOnly={!isBar}
    />
  );

  const leftCluster = (
    <>
      {sessionChrome}
      {liveDebate ? null : mentionButton}
    </>
  );

  // 生成中：停止常显。有草稿时复用空闲「发送」钮（Enter = 排队，挂在输入框上方）。
  // 整轮停在这枚停止钮。Ctrl/Cmd+Enter 只在队还在或工具步还在执行时送进当前回合。
  // 辩论进行中：发送=continue；隐藏排队；收场靠裁判收敛，不在此露出结论。
  // 离线 / 只读协作桌硬禁用发送（按钮 disabled + title；键盘走 handleKeyDown）。
  // 辩论进行中主框是「对这场说话」：发送只看正文。@ 入口已藏，mention 芯片不得单独点亮发送。
  const hasDraft = liveDebate
    ? Boolean(plainText(value).trim())
    : composerHasSendableDraft(value, attachments, agentMentions);
  const sendReady = !sendBlocked && (hasDraft || isSending);
  const midFlightHint =
    teamLive || toolStepOpen
      ? "排队至本回合结束后发送（Enter）；Ctrl/Cmd+Enter 送进当前回合"
      : "排队至本回合结束后发送";
  const stopLabel = isStopping ? "停止中…" : "停止生成";
  const stopButton = (
    <IconButton
      size="md"
      tone="inverse"
      onClick={stopGeneration}
      aria-label={stopLabel}
      title={stopLabel}
      aria-busy={isStopping || undefined}
      className={isStopping ? "touch-target opacity-75" : "touch-target"}
    >
      {isStopping ? (
        <Loader2 size={16} className="animate-spin" aria-hidden />
      ) : (
        <Square size={16} aria-hidden />
      )}
    </IconButton>
  );
  const sendButton = (readyTitle?: string) => (
    <IconButton
      size="md"
      tone={sendReady ? "inverse" : "muted"}
      onClick={() => void handleSend()}
      disabled={!hasDraft || sendBlocked || isSending}
      aria-label="发送"
      aria-busy={isSending || undefined}
      data-sending={isSending ? "true" : undefined}
      className="touch-target"
      title={
        sendBlocked ? sendBlockedTitle : isSending ? "发送中…" : readyTitle
      }
    >
      {isSending ? (
        <Loader2 size={16} className="animate-spin" aria-hidden />
      ) : (
        <Send size={16} />
      )}
    </IconButton>
  );
  const primarySendButton = sendButton();
  const classicMidFlightSend = (
    <div className="flex items-center gap-1.5">
      {sendButton(midFlightHint)}
      {stopButton}
    </div>
  );
  // 辩论进行中：隐藏排队/插队；发送=continue。勿扫正文猜「够了收」，勿另放收场键。
  const sendControls = liveDebate ? (
    <div className="flex items-center gap-1.5">
      {hasDraft ? primarySendButton : null}
      {isGenerating || isStopping ? stopButton : null}
    </div>
  ) : deskOccupied ? (
    hasDraft ? (
      classicMidFlightSend
    ) : (
      stopButton
    )
  ) : (
    primarySendButton
  );

  const editorBlock = (
    <div ref={bodyHostRef} className="relative min-w-0 flex-1">
      {voice.isRecording && voice.interimText && (
        <div
          aria-hidden
          className={`pointer-events-none absolute inset-0 overflow-hidden text-sm whitespace-pre-wrap break-words ${
            isBar ? "px-2 py-2" : "px-4 pt-3 pb-1"
          }`}
        >
          <span className="invisible">{plainText(value)}</span>
          <span className="text-foreground/40">{voice.interimText}</span>
        </div>
      )}
      <ComposerBodyEditor
        ref={bodyRef}
        value={value}
        attachments={attachments}
        agentMentions={agentMentions}
        placeholder={resolvedPlaceholder}
        className={isBar ? "px-2 py-2" : "px-4 pt-3 pb-1"}
        maxLength={MESSAGE_CHAR_LIMIT}
        onChange={handleBodyChange}
        onReconcile={handleReconcile}
        onRemoveAttachment={removeAttachment}
        onRemoveAgent={removeAgentMention}
        onCaret={handleCaret}
        onKeyDown={handleKeyDown}
        onPaste={drop.handlePaste}
        onFocus={() => {
          void warmLlmHttpOnComposerFocus(conversationId);
        }}
      />
    </div>
  );

  return (
    <div data-composer-shell="" className="relative w-full">
      <div className="flex min-w-0 w-full flex-col">
        {failureNotice && (
          <ComposerFailureBanner
            message={failureNotice.message}
            action={failureNotice.action}
            onDismiss={failureNotice.onDismiss}
          />
        )}
        <div
          className={`relative min-w-0 w-full border bg-card shadow-sm transition-colors ${
            attachedBelowApproval
              ? "rounded-b-xl rounded-t-none border-t-0"
              : "rounded-xl"
          } ${
            drop.dragOver
              ? "border-primary ring-2 ring-primary/40"
              : "border-border"
          }`}
          onDragOver={drop.handleDragOver}
          onDragLeave={drop.handleDragLeave}
          onDrop={drop.handleDrop}
          data-composer-variant={variant}
          data-composer-attached-approval={
            attachedBelowApproval ? "true" : undefined
          }
        >
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            multiple
            tabIndex={-1}
            aria-hidden
            onChange={(e) => void onBrowserFilesSelected(e)}
          />
          {drop.dragOver && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-card/80 text-sm font-medium text-primary">
              拖放文件以添加为附件
            </div>
          )}
          {drop.dropError && (
            <output
              aria-live="polite"
              className="flex items-start gap-2 px-3 pt-2 text-xs text-muted-foreground"
            >
              <span className="min-w-0 flex-1">{drop.dropError}</span>
              <button
                type="button"
                className="shrink-0 rounded-lg p-0.5 text-muted-foreground hover:bg-transparent hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label="关闭提示"
                onClick={drop.clearDropError}
              >
                <X size={12} />
              </button>
            </output>
          )}
          {menuOpen && !liveDebate && (
            <MentionMenu
              placement={isBar ? "above" : "below"}
              sections={mention.sections}
              flatItems={mention.flatItems}
              activeIndex={mention.activeIndex}
              loading={mention.indexLoading}
              error={mention.menuError}
              query={mention.query}
              showSearch={mention.menuMode === "browse"}
              noFileSources={
                mention.indexLoadedRef.current && mention.sourceCount === 0
              }
              showCategoryLevel={mention.showCategoryLevel}
              categories={mention.categories}
              canGoBack={mention.canGoBack}
              focusedSectionLabel={mention.focusedSectionLabel}
              onQueryChange={mention.setQuery}
              onKeyDown={(e) => {
                mention.handleMenuNavKey(e);
              }}
              onSelect={(item) => mention.selectItem(item)}
              onHover={mention.setActiveIndex}
              onDrill={mention.drillCategory}
              onAttach={() => void mention.pickLocalFile()}
              onBack={mention.goBack}
              onAddRoot={mention.handleAddRoot}
              searchInputRef={mention.searchInputRef}
            />
          )}

          {!conversationId && assignHint && (
            <DraftWorkspaceAssignPrompt
              attachmentFolderName={assignHint.folderName}
              currentFolderName={currentFolderName}
              onAssign={acceptAssignHint}
              onKeep={dismissAssignHint}
            />
          )}

          {/* 失败横幅亮着时，连接 / 看图 / 压缩让位。 */}
          {!failureNotice && (
            <>
              <ComposerConnectionNotice />
              <ComposerVisionHint />
              <ComposerContextCompactedHint />
            </>
          )}

          {voice.isRecording && (
            <RecordingBar duration={voice.duration} onCancel={voice.cancel} />
          )}

          {isBar ? (
            <div
              className={`${COMPOSER_BAR_ROW} px-2`}
              data-composer-actions=""
            >
              <div className={COMPOSER_BAR_CLUSTER}>
                <ComposerPlusMenu>
                  {sessionChrome}
                  {liveDebate ? null : mentionButton}
                </ComposerPlusMenu>
              </div>
              {editorBlock}
              <div className={`${COMPOSER_BAR_CLUSTER} gap-1`}>
                {voice.isSupported && (
                  <VoiceButton state={voice.state} onClick={voice.toggle} />
                )}
                {sendControls}
              </div>
            </div>
          ) : (
            <>
              {editorBlock}
              <div
                className="flex items-center justify-between px-4 pb-3"
                data-composer-actions=""
              >
                <div className="flex min-w-0 flex-1 items-center gap-1">
                  {leftCluster}
                </div>
                <div className="flex items-center gap-1">
                  {voice.isSupported && (
                    <VoiceButton state={voice.state} onClick={voice.toggle} />
                  )}
                  {sendControls}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
      <div
        data-composer-endcap={isBar ? "bar" : "card"}
        className={`${COMPOSER_ENDCAP_PLACE} ${isBar ? COMPOSER_BAR_ROW : COMPOSER_CARD_ENDCAP}`}
      >
        <div className={isBar ? COMPOSER_BAR_CLUSTER : "flex items-center"}>
          <ComposerReceivedContextButton />
        </div>
      </div>
    </div>
  );
}

/** @ 按钮：bar「＋」菜单内带文案；card 底栏仅图标。点菜单内项时先关＋再插入/开关 mention。 */
function ComposerMentionButton({
  onToggle,
  iconOnly = true,
}: {
  onToggle: () => void;
  iconOnly?: boolean;
}) {
  const plusHost = useComposerPlusHost();
  const closePlus = useComposerPlusClose();
  const onClick = () => {
    closePlus?.();
    onToggle();
  };
  if (plusHost && plusHost.panel !== "list") return null;
  if (iconOnly) {
    return (
      <IconButton size="md" onClick={onClick} aria-label="@ 引用">
        <AtSign size={16} />
      </IconButton>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="@ 引用"
      className="inline-flex h-8 w-full items-center gap-1.5 rounded-lg px-2 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
    >
      <AtSign size={14} className="shrink-0" aria-hidden />
      <span>引用</span>
    </button>
  );
}
