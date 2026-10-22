import { MentionMenu } from "@/components/chat/MentionMenu";
import {
  type AttachSourceHandle,
  AttachSourceInputs,
  AttachSourcePopover,
} from "@/components/chat/message-input/AttachSourceInputs";
import {
  ComposerBodyEditor,
  type ComposerBodyHandle,
} from "@/components/chat/message-input/ComposerBodyEditor";
import { forgetAttachmentUpload } from "@/components/chat/message-input/attachmentUploads";
import {
  type PendingAgentMention,
  type PendingAttachment,
  composerHasSendableDraft,
} from "@/components/chat/message-input/composerAttachments";
import { settleAttachments } from "@/components/chat/message-input/settleAttachments";
import { useComposerDrop } from "@/components/chat/message-input/useComposerDrop";
import { useMentionMenu } from "@/components/chat/message-input/useMentionMenu";
import { ImageThumbStrip } from "@/components/files/ImageThumbStrip";
import { useConversationFileSourceState } from "@/hooks/useConversationFileSource";
import { hasNativeMediaCapture } from "@/lib/capabilities";
import { handlePlainPaste } from "@/lib/clipboardPlain";
import { dropInlineIndex, insertInlineToken } from "@/lib/inlineBody";
import { notifyError } from "@/lib/toast";
import type {
  OutgoingAgentMention,
  OutgoingAttachment,
} from "@/services/streamConversation";
import { editQueuedTurn } from "@/services/turns/cancelQueuedTurn";
import { sendMidFlightMessage } from "@/services/turns/midFlight";
import { AtSign, Paperclip } from "lucide-react";
import { useEffect, useRef, useState } from "react";

const MESSAGE_CHAR_LIMIT = 32_000;

export function pendingAttachments(
  attachments: readonly OutgoingAttachment[],
): PendingAttachment[] {
  return attachments.map((att, index) => {
    const id = att.workspace_path || `${att.path}:${att.name}:${index}`;
    return {
      id,
      key: id,
      name: att.name,
      path: att.path,
      text: att.text,
      truncated: att.truncated,
      kind: att.kind ?? "file",
      conversationId: att.conversation_id,
      documentId: att.document_id,
      workspacePath: att.workspace_path,
      binary: att.binary,
      sourceFolderId: att.source_folder_id,
    };
  });
}

export function pendingMentions(
  mentions: readonly OutgoingAgentMention[],
): PendingAgentMention[] {
  return mentions.map((mention, index) => ({
    id: `${mention.agent_id}:${index}`,
    agentId: mention.agent_id,
    role: mention.role,
  }));
}

function outgoingMentions(
  mentions: readonly PendingAgentMention[],
): OutgoingAgentMention[] {
  return mentions.slice(0, 10).map((mention) => ({
    agent_id: mention.agentId,
    role: mention.role,
  }));
}

/**
 * 排队行展开后的编辑面：正文、附件、@。
 * 保存写回同一条；已开跑则留下草稿，由用户再发成一条新的。
 */
export function QueuedTurnEditor({
  conversationId,
  queueId,
  initialContent,
  initialAttachments,
  initialMentions,
  detached,
  draftRef,
  onDiscard,
  onSaved,
  onDetached,
}: {
  conversationId: string;
  queueId: string;
  initialContent: string;
  initialAttachments: PendingAttachment[];
  initialMentions: PendingAgentMention[];
  detached: boolean;
  draftRef: {
    current: {
      content: string;
      attachments: PendingAttachment[];
      mentions: PendingAgentMention[];
    } | null;
  };
  onDiscard: () => void;
  onSaved: () => void;
  onDetached: () => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<ComposerBodyHandle>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachSourceRef = useRef<AttachSourceHandle>(null);
  const nativeCapture = hasNativeMediaCapture();
  const pendingCaretRef = useRef<number | null>(null);
  const [value, setValue] = useState(initialContent);
  const [attachments, setAttachments] = useState(initialAttachments);
  const [agentMentions, setAgentMentions] = useState(initialMentions);
  const { source: fileSource, pending: fileSourcePending } =
    useConversationFileSourceState(conversationId);
  draftRef.current = { content: value, attachments, mentions: agentMentions };
  const [busy, setBusy] = useState(false);

  const insertTokenAtCaret = (kind: "A" | "M", index: number) => {
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
  };

  const mention = useMentionMenu({
    conversationId,
    value,
    setValue,
    attachments,
    setAttachments,
    agentMentions,
    setAgentMentions,
    bodyRef,
    onBrowserFilePick: () =>
      nativeCapture
        ? attachSourceRef.current?.pickFiles()
        : fileInputRef.current?.click(),
  });

  const drop = useComposerDrop(
    attachments,
    setAttachments,
    conversationId,
    (index) => insertTokenAtCaret("A", index),
  );

  useEffect(() => {
    bodyRef.current?.focus();
  }, []);

  useEffect(() => {
    const onPointer = (event: MouseEvent) => {
      const root = rootRef.current;
      if (!root || root.contains(event.target as Node)) return;
      onDiscard();
    };
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [onDiscard]);

  const sendable =
    composerHasSendableDraft(value, attachments, agentMentions) &&
    attachments.every((att) => !att.uploadState);
  const menuOpen = mention.menuMode !== null;

  const settle = async () => {
    const settled = await settleAttachments(
      conversationId,
      attachments,
      "midflight",
    );
    if (!settled.ok) {
      notifyError(new Error(settled.reason), "附件还没准备好");
      return null;
    }
    return {
      content: value,
      attachments: settled.outgoing,
      agentMentions: outgoingMentions(agentMentions),
    };
  };

  const save = async () => {
    if (busy || !sendable || detached) return;
    setBusy(true);
    try {
      const payload = await settle();
      if (!payload) return;
      const outcome = await editQueuedTurn(conversationId, queueId, payload);
      if (outcome === "already_gone") onDetached();
      else onSaved();
    } catch (err) {
      notifyError(err, "修改排队失败");
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    if (busy || !sendable) return;
    setBusy(true);
    try {
      const payload = await settle();
      if (!payload) return;
      const result = await sendMidFlightMessage(
        conversationId,
        payload.content,
        payload.attachments,
        "queue",
        payload.agentMentions,
      );
      if (result.kind === "error" || result.kind === "blocked") {
        notifyError(new Error("这条没能再发出去"), "再发失败");
        return;
      }
      onSaved();
    } catch (err) {
      notifyError(err, "再发失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      ref={rootRef}
      className="flex flex-col gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2"
      data-testid="queued-turn-editor"
      data-queue-id={queueId}
      data-detached={detached ? "true" : undefined}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || event.nativeEvent.isComposing || menuOpen)
          return;
        event.preventDefault();
        onDiscard();
      }}
      onDragOver={drop.handleDragOver}
      onDragLeave={drop.handleDragLeave}
      onDrop={drop.handleDrop}
    >
      {nativeCapture ? (
        <AttachSourceInputs
          ref={attachSourceRef}
          onPicked={(files) => {
            mention.clearActiveMention();
            void drop.attachFiles(files);
          }}
        />
      ) : (
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          multiple
          tabIndex={-1}
          aria-hidden
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            event.target.value = "";
            if (files.length === 0) return;
            mention.clearActiveMention();
            void drop.attachFiles(files);
          }}
        />
      )}
      {detached ? (
        <p className="text-xs text-muted-foreground">这条已经开始了</p>
      ) : null}
      {drop.dropError ? (
        <p className="text-xs text-muted-foreground">{drop.dropError}</p>
      ) : null}
      {menuOpen ? (
        <MentionMenu
          placement="above"
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
          onKeyDown={(event) => {
            mention.handleMenuNavKey(event);
          }}
          onSelect={(item) => mention.selectItem(item)}
          onHover={mention.setActiveIndex}
          onDrill={mention.drillCategory}
          onAttach={() => void mention.pickLocalFile()}
          onBack={mention.goBack}
          onAddRoot={mention.handleAddRoot}
          searchInputRef={mention.searchInputRef}
        />
      ) : null}
      <ImageThumbStrip
        attachments={attachments}
        source={fileSource}
        sourcePending={fileSourcePending}
        onRemove={(id) => {
          const index = attachments.findIndex((att) => att.id === id);
          forgetAttachmentUpload(id);
          if (index >= 0) {
            setValue((prev) => dropInlineIndex(prev, "attachment", index));
          }
          setAttachments((prev) => prev.filter((att) => att.id !== id));
        }}
        className="px-2 pt-2"
      />
      <ComposerBodyEditor
        ref={bodyRef}
        value={value}
        attachments={attachments}
        agentMentions={agentMentions}
        placeholder="修改这条排队"
        className="px-2 py-2 text-sm"
        maxLength={MESSAGE_CHAR_LIMIT}
        onChange={(next) => {
          setValue(next);
          mention.syncMention(next, bodyRef.current?.getCaret() ?? next.length);
        }}
        onReconcile={(nextAtts, nextMents) => {
          const removed = attachments.filter(
            (att) => !nextAtts.some((next) => next.id === att.id),
          );
          for (const att of removed) forgetAttachmentUpload(att.id);
          setAttachments(nextAtts);
          setAgentMentions(nextMents);
        }}
        onRemoveAttachment={(id) => {
          const index = attachments.findIndex((att) => att.id === id);
          forgetAttachmentUpload(id);
          if (index >= 0) {
            setValue((prev) => dropInlineIndex(prev, "attachment", index));
          }
          setAttachments((prev) => prev.filter((att) => att.id !== id));
        }}
        onRemoveAgent={(id) => {
          const index = agentMentions.findIndex((mention) => mention.id === id);
          if (index >= 0) {
            setValue((prev) => dropInlineIndex(prev, "mention", index));
          }
          setAgentMentions((prev) =>
            prev.filter((mention) => mention.id !== id),
          );
        }}
        onCaret={(caret) => mention.syncMention(value, caret)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (mention.menuMode && mention.handleMenuNavKey(event)) {
            event.stopPropagation();
            return;
          }
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (detached) void resend();
            else void save();
          }
        }}
        onPaste={(event) => {
          drop.handlePaste(event);
          if (event.defaultPrevented) return;
          handlePlainPaste(event, {
            maxLength: MESSAGE_CHAR_LIMIT,
            currentLength: value.length,
            selectedLength: 0,
          });
        }}
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="shrink-0 rounded-lg p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="@ 引用"
          disabled={busy}
          onClick={() => mention.toggleAtMention()}
        >
          <AtSign size={14} />
        </button>
        {nativeCapture ? (
          <AttachSourcePopover
            trigger={
              <button
                type="button"
                className="shrink-0 rounded-lg p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                aria-label="添加附件"
                disabled={busy}
              >
                <Paperclip size={14} />
              </button>
            }
            onCamera={() => attachSourceRef.current?.pickCamera()}
            onGallery={() => attachSourceRef.current?.pickGallery()}
            onFiles={() => attachSourceRef.current?.pickFiles()}
          />
        ) : (
          <button
            type="button"
            className="shrink-0 rounded-lg p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="添加附件"
            disabled={busy}
            onClick={() => fileInputRef.current?.click()}
          >
            <Paperclip size={14} />
          </button>
        )}
        <span className="min-w-0 flex-1" />
        <button
          type="button"
          className="shrink-0 rounded-lg px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={onDiscard}
          disabled={busy}
        >
          放弃
        </button>
        {detached ? (
          <button
            type="button"
            className="shrink-0 rounded-lg px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            data-testid="queued-turn-resend"
            disabled={busy || !sendable}
            onClick={() => void resend()}
          >
            再发一条
          </button>
        ) : (
          <button
            type="button"
            className="shrink-0 rounded-lg px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            data-testid="queued-turn-save"
            disabled={busy || !sendable}
            onClick={() => void save()}
          >
            保存
          </button>
        )}
      </div>
    </div>
  );
}
