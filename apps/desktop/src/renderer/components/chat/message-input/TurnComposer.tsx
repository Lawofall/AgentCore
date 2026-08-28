import { DraftWorkspaceAssignPrompt } from "@/components/chat/DraftWorkspaceAssignPrompt";
import { MentionMenu } from "@/components/chat/MentionMenu";
import { Button, IconButton } from "@/components/ui";
import { useConversations } from "@/hooks/useConversations";
import { useFolders } from "@/hooks/useFolders";
import { copyText } from "@/lib/clipboard";
import {
  COMPOSER_CONTINUE_PLACEHOLDER,
  COMPOSER_EMPTY_INTERRUPTED_HINT,
  isContinuableAssistant,
} from "@/lib/composerContinueHint";
import {
  dropInlineIndex,
  insertInlineToken,
  migrateLegacyDraft,
  plainText,
} from "@/lib/inlineBody";
import {
  buildSupportDiagnosticPack,
  formatSupportDiagnosticText,
  precedingUserMessageId,
  supportDiagnosticExtrasFromError,
} from "@/lib/supportDiagnostics";
import { notifySuccess } from "@/lib/toast";
import {
  assistantHasTeamStrip,
  turnOutcomeForAssistant,
} from "@/lib/turnOutcome";
import { draftKeyFor, useComposerDraftStore } from "@/stores/composer";
import {
  assistantProjectionId,
  getActiveRuntime,
  useActiveError,
  useActiveGenerating,
  useActiveTurnPhase,
  useConversationStore,
} from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { useFoldersStore } from "@/stores/folders";
import { isColdResumeKind, usePendingApprovals } from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import { useServerHealthStore } from "@/stores/serverHealth";
import { AtSign, Copy, ListPlus, Loader2, Send, Square, X } from "lucide-react";
import type { ChangeEvent, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ComposerBodyEditor,
  type ComposerBodyHandle,
} from "./ComposerBodyEditor";
import { ComposerCloudBridgeHint } from "./ComposerCloudBridgeHint";
import { ComposerContextCompactedHint } from "./ComposerContextCompactedHint";
import { ComposerGitStatusChip } from "./ComposerGitStatusChip";
import { ComposerPendingHintNotice } from "./ComposerPendingHintNotice";
import {
  ComposerPlusMenu,
  useComposerPlusClose,
  useComposerPlusHost,
} from "./ComposerPlusMenu";
import { ComposerSendErrorNotice } from "./ComposerSendErrorNotice";
import { ComposerWorkspaceChip } from "./ComposerWorkspaceChip";
import { ModelPicker } from "./ModelPicker";
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
import { decideDraftFolderAssign } from "./resolveAttachmentFolder";
import { useComposerDrop } from "./useComposerDrop";
import { useComposerSend } from "./useComposerSend";
import type { AttachmentFolderHint } from "./useMentionMenu";
import { useMentionMenu } from "./useMentionMenu";
import { useVoiceInput } from "./useVoiceInput";

const EMPTY_ATTACHMENTS: PendingAttachment[] = [];
const EMPTY_AGENT_MENTIONS: PendingAgentMention[] = [];

// 输入框自增高边界：card 空/单行草稿保底 ~2 行（text-sm 20px 行高 + pt-3/pb-1 = 56px）；
// bar 默认一行高（20px 行高 + py-2 = 36px）。上限 200px 后转内部滚动。
const MIN_COMPOSER_HEIGHT_CARD = 56;
const MIN_COMPOSER_HEIGHT_BAR = 36;
const MAX_COMPOSER_HEIGHT = 200;

/** Align with backend `MessageCreate.content` max_length. */
const MESSAGE_CHAR_LIMIT = 32_000;
/** Show the counter only when the draft is near the limit (bar mode). */
const CHAR_COUNT_NEAR_LIMIT = 28_000;

export type TurnComposerVariant = "card" | "bar";

/**
 * The ONE turn composer (统一 AI 输入框): the full-featured card — auto-growing
 * textarea, @ 引用（含本机附件）, drag-drop attachments, 停止生成,
 * char count, 回填 channel — hosted by the chat view's
 * {@link import("../MessageInput").MessageInput}. Canvas is look-only; 下达指令
 * stays in chat. Hosts only pick chrome (placeholder).
 *
 * `variant="bar"` is the compact single-row chrome used only by the chat bottom dock:
 * `[＋]` · textarea · 语音 · 发送；工作区/Git/模型/权限/@ 收进＋菜单。
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
  const turnPhase = useActiveTurnPhase();
  const isStopping = turnPhase === "stopping";
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const conversations = useConversations();
  const contextCompacted = Boolean(
    conversationId &&
      conversations.find((c) => c.id === conversationId)?.contextCompacted,
  );
  const hasPausedDecision = usePausedTurnStore((s) =>
    conversationId
      ? s.pending.some(
          (p) =>
            p.conversationId === conversationId && isColdResumeKind(p.kind),
        )
      : false,
  );
  const pendingApprovals = usePendingApprovals(conversationId);
  const lastMessage = useConversationStore((s) => {
    const id = s.currentConversationId;
    if (!id) return null;
    return s.byId[id]?.messages.at(-1) ?? null;
  });
  const showPendingHint =
    !!conversationId &&
    !isGenerating &&
    (hasPausedDecision || pendingApprovals.length > 0);
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
    !isGenerating && Boolean(lastOutcome?.showComposerHint);
  const supportDiagnosticIds =
    lastMessage?.role === "assistant"
      ? {
          conversationId,
          messageId: assistantProjectionId(lastMessage),
          userMessageId: precedingUserMessageId(
            getActiveRuntime().messages,
            lastMessage.id,
          ),
          traceId: lastMessage.traceId,
          executionId: lastMessage.executionId,
          ...supportDiagnosticExtrasFromError(lastMessage.error),
        }
      : null;
  const supportDiagnosticText = supportDiagnosticIds
    ? formatSupportDiagnosticText(supportDiagnosticIds)
    : "";
  const copySupportDiagnostics = () => {
    if (!supportDiagnosticIds || !supportDiagnosticText) return;
    void buildSupportDiagnosticPack(supportDiagnosticIds).then((text) => {
      if (!text) return;
      void copyText(text).then((ok) => {
        if (ok) notifySuccess("已复制排查包");
      });
    });
  };
  const serverStatus = useServerHealthStore((s) => s.status);
  const serverUnhealthy = serverStatus === "offline";
  const resolvedPlaceholder = useMemo(() => {
    if (!isGenerating && isContinuableAssistant(lastMessage)) {
      return COMPOSER_CONTINUE_PLACEHOLDER;
    }
    return placeholder;
  }, [isGenerating, lastMessage, placeholder]);
  const draftKey = draftKeyFor(conversationId);
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
  const draftIntent = useFoldersStore((s) => s.draftWorkspaceIntent);
  const pendingFolderId =
    draftIntent.kind === "folder" ? draftIntent.folderId : null;
  const dismissedAssignRef = useRef<Set<string>>(new Set());
  const [assignHint, setAssignHint] = useState<AttachmentFolderHint | null>(
    null,
  );

  const handleAttachmentFolderHint = useCallback(
    (hint: AttachmentFolderHint) => {
      const store = useFoldersStore.getState();
      const decision = decideDraftFolderAssign(hint, store.draftWorkspaceIntent);
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
    isGenerating,
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
      mention.syncMention(next, bodyRef.current?.getCaret() ?? next.length);
      if (drop.dropError) drop.clearDropError();
    },
    [drop, mention, setValue],
  );

  const handleCaret = useCallback(
    (caret: number) => {
      mention.syncMention(value, caret);
    },
    [mention, value],
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

    if (mention.menuMode && mention.handleMenuNavKey(e)) return;

    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      // 生成中强制 steer（插队）；空闲与 Enter 同路径（默认 steer），勿伪装传 queue。
      if (serverUnhealthy) return;
      if (isGenerating) {
        void handleSend({ delivery: "steer" });
      } else {
        void handleSend();
      }
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      // N4-A：离线硬禁用（与发送按钮一致；handleSend 仍有兜底）。
      if (serverUnhealthy) return;
      // 空闲默认 steer；生成中默认 queue（排队）。插队见 Ctrl/Cmd+Enter / 「插队」。
      void handleSend();
    }
  };

  const charCount = value.length;
  const menuOpen = mention.menuMode !== null;
  const showCharCount = isBar
    ? charCount >= CHAR_COUNT_NEAR_LIMIT
    : charCount > 0;

  // 左簇顺序：工作区 · Git? · 模型 · 权限 · @
  // bar：整簇收进 ComposerPlusMenu（权限/@ 带文案）；card：底栏摊开（iconOnly）。
  // 否决 Composer 并排「本地引擎/云端过桥」切换器；过桥事后弱提示见 ComposerCloudBridgeHint。
  const sessionChrome = (
    <>
      <ComposerWorkspaceChip conversationId={conversationId} />
      <ComposerGitStatusChip conversationId={conversationId} />
      <ModelPicker disabled={isGenerating} />
      <PermissionAxesBadge disabled={isGenerating} iconOnly={!isBar} />
    </>
  );

  // 生成中照常可开 @：插话 / 排队本就带附件走，禁用只会让人以为坏了。
  const mentionButton = (
    <ComposerMentionButton
      onToggle={mention.toggleAtMention}
      iconOnly={!isBar}
    />
  );

  const leftCluster = (
    <>
      {sessionChrome}
      {mentionButton}
    </>
  );

  // 生成中：停止常显（对齐手机 send+stop 并存）；有草稿时再加「插队」次级 +「排队」主键。
  // 插队 = 显式 steer（下一步生效），不把主槽改成 Stop&send。
  // N4-A：只读离线硬禁用发送。
  const sendBlocked = serverUnhealthy;
  const hasDraft = composerHasSendableDraft(value, attachments, agentMentions);
  const queueDisabled = !hasDraft || sendBlocked || isSending;
  const midFlightLabel = "排队发送";
  const midFlightHint = "排队至本回合结束后发送（Enter）；Ctrl/Cmd+Enter 插队";
  const stopLabel = isStopping ? "停止中…" : "停止生成";
  const stopButton = (
    <IconButton
      size="sm"
      tone="destructive"
      onClick={stopGeneration}
      aria-label={stopLabel}
      title={stopLabel}
      aria-busy={isStopping || undefined}
      className={isStopping ? "opacity-75" : undefined}
    >
      {isStopping ? (
        <Loader2 size={16} className="animate-spin" aria-hidden />
      ) : (
        <Square size={16} aria-hidden />
      )}
    </IconButton>
  );
  const sendControls = isGenerating ? (
    hasDraft ? (
      <div className="flex items-center gap-1.5">
        <Button
          variant="neutral"
          size="sm"
          className="border-border text-foreground"
          onClick={() => void handleSend({ delivery: "steer" })}
          disabled={queueDisabled}
          aria-label="插队"
          title={
            sendBlocked
              ? "离线时无法发送"
              : "插队：下一步生效（Ctrl/Cmd+Enter）；协调模式下 CEO 仍可能改排队"
          }
          data-testid="composer-steer-link"
        >
          插队
        </Button>
        <Button
          variant="primary"
          size="sm"
          icon={<ListPlus size={14} aria-hidden />}
          onClick={() => void handleSend()}
          disabled={queueDisabled}
          aria-label={midFlightLabel}
          title={sendBlocked ? "离线时无法发送" : midFlightHint}
        >
          排队
        </Button>
        {stopButton}
      </div>
    ) : (
      stopButton
    )
  ) : (
    <IconButton
      size="sm"
      tone="primary"
      onClick={() => void handleSend()}
      disabled={!hasDraft || sendBlocked || isSending}
      aria-label="发送"
      aria-busy={isSending || undefined}
      data-sending={isSending ? "true" : undefined}
      title={sendBlocked ? "离线时无法发送" : isSending ? "发送中…" : undefined}
    >
      {isSending ? (
        <Loader2 size={16} className="animate-spin" aria-hidden />
      ) : (
        <Send size={16} />
      )}
    </IconButton>
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
      />
    </div>
  );

  return (
    <div
      className={`relative border bg-card shadow-sm transition-colors ${
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
      <ComposerSendErrorNotice
        draftKey={draftKey}
        suppressSession={Boolean(lastOutcome && !lastOutcome.showSessionBanner)}
        onCopySupportPack={
          lastOutcome?.supportPackHost === "session" && supportDiagnosticText
            ? copySupportDiagnostics
            : undefined
        }
      />
      {menuOpen && (
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

      {/* 断连提示：仅在心跳判定服务器不可达时出现，主动告知「发送前」状态。 */}
      <ComposerConnectionNotice />

      {/* 本机绑定却本轮过桥：弱状态（非引擎切换器；强制关路径不展示）。 */}
      <ComposerCloudBridgeHint />

      {/* 会话字段徽章：较早对话已压缩（旗标 only，无摘要正文）。 */}
      <ComposerContextCompactedHint show={contextCompacted} />

      {/* 挂起弱提示：有待确认/续跑卡时常驻；不强拦发送（发送前二次确认见 useComposerSend）。 */}
      <ComposerPendingHintNotice show={showPendingHint} />

      {/* 输入区轻提示：空中断 send_next / 部分完成+限流 wait_then_retry。发送下一条即恢复。报障跟 supportPackHost。 */}
      {showComposerHint && (
        <div
          aria-live="polite"
          data-testid="composer-empty-interrupted-hint"
          className="flex items-center gap-1.5 px-4 pt-2 text-xs text-muted-foreground"
        >
          <span className="min-w-0 flex-1">
            {lastOutcome?.message ?? COMPOSER_EMPTY_INTERRUPTED_HINT}
          </span>
          {lastOutcome?.supportPackHost === "composer" &&
            supportDiagnosticText && (
              <Button
                variant="ghost"
                className="shrink-0 text-muted-foreground hover:bg-transparent hover:text-foreground"
                icon={<Copy size={13} />}
                onClick={copySupportDiagnostics}
              >
                复制排查包
              </Button>
            )}
        </div>
      )}

      {voice.isRecording && (
        <RecordingBar duration={voice.duration} onCancel={voice.cancel} />
      )}

      {isBar ? (
        <div className="flex items-end gap-1 px-2 py-1">
          <div className="flex shrink-0 items-center pb-0.5">
            <ComposerPlusMenu>
              {sessionChrome}
              {mentionButton}
            </ComposerPlusMenu>
          </div>
          {editorBlock}
          <div className="flex shrink-0 items-center gap-1 pb-0.5">
            {voice.isSupported && (
              <VoiceButton state={voice.state} onClick={voice.toggle} />
            )}
            {showCharCount && (
              <span className="text-xs text-muted-foreground">
                {charCount}/{MESSAGE_CHAR_LIMIT}
              </span>
            )}
            {sendControls}
          </div>
        </div>
      ) : (
        <>
          {editorBlock}
          <div className="flex items-center justify-between px-4 pb-3">
            <div className="flex min-w-0 flex-1 items-center gap-1">
              {leftCluster}
            </div>
            <div className="flex items-center gap-3">
              {voice.isSupported && (
                <VoiceButton state={voice.state} onClick={voice.toggle} />
              )}
              {showCharCount && (
                <span className="text-xs text-muted-foreground">
                  {charCount}字
                </span>
              )}
              {sendControls}
            </div>
          </div>
        </>
      )}
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
      className="inline-flex h-8 w-full items-center gap-1.5 rounded-lg px-2 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground"
    >
      <AtSign size={14} className="shrink-0" aria-hidden />
      <span>引用</span>
    </button>
  );
}
