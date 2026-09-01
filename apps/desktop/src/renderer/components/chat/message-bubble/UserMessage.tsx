import {
  CollapsibleSpeech,
  USER_BUBBLE_COLLAPSED_MAX_H,
} from "@/components/chat/debate/CollapsibleSpeech";
import { Button } from "@/components/ui";
import { hasInlineMarkers, renderInlineLabels } from "@/lib/inlineBody";
import { runRegenerate } from "@/services/turns";
import {
  useActiveGenerating,
  useConversationStore,
} from "@/stores/conversation";
import { useQueuedTurns } from "@/stores/queuedTurns";
import { Check, Copy, Pencil, X } from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { MessageAction, MessageTime } from "./MessageActions";
import { SyncStatusHint } from "./SyncStatusHint";
import {
  UserChipTray,
  UserInlineBody,
  UserInlineDraft,
  type UserInlineDraftHandle,
} from "./UserInlineBody";
import type { MessageBubbleProps } from "./types";
import { useCopyAction } from "./useCopyAction";

export function UserMessage({ message }: MessageBubbleProps) {
  const isGenerating = useActiveGenerating();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);
  const editRef = useRef<HTMLTextAreaElement>(null);
  const draftRef = useRef<UserInlineDraftHandle>(null);
  const attachments = message.attachments ?? [];
  const agentMentions = message.agentMentions ?? [];
  const { copied, onCopy } = useCopyAction(() =>
    renderInlineLabels(message.content, attachments, agentMentions),
  );
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const queuedHere = useQueuedTurns(conversationId).some(
    (entry) => entry.messageId === message.id,
  );
  const marked = hasInlineMarkers(message.content) || hasInlineMarkers(draft);

  const startEdit = () => {
    setDraft(message.content);
    setEditing(true);
  };

  useEffect(() => {
    if (editing && !marked) {
      const el = editRef.current;
      if (el) {
        el.focus();
        el.selectionStart = el.selectionEnd = el.value.length;
        el.style.height = "0";
        el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
      }
    }
  }, [editing, marked]);

  const submitEdit = () => {
    const flushed = marked ? draftRef.current?.flush() : null;
    const nextContent = (flushed?.content ?? draft).trim();
    const nextAtts = flushed?.attachments ?? attachments;
    const nextMents = flushed?.mentions ?? agentMentions;
    if (!nextContent && nextAtts.length === 0) return;
    setEditing(false);
    const sameContent = nextContent === (message.content ?? "").trim();
    const sameAtts =
      nextAtts.length === attachments.length &&
      nextAtts.every((a, i) => a.id === attachments[i]?.id);
    const sameMents =
      nextMents.length === agentMentions.length &&
      nextMents.every((m, i) => m.agentId === agentMentions[i]?.agentId);
    if (sameContent && sameAtts && sameMents) return;
    if (!conversationId) return;
    useConversationStore.getState().updateMessage(
      message.id,
      {
        content: nextContent,
        attachments: nextAtts,
        agentMentions: nextMents,
      },
      conversationId,
    );
    void runRegenerate(message.id, nextContent, {
      attachments: nextAtts,
      agentMentions: nextMents,
    });
  };

  const onEditKeyDown = (
    e: KeyboardEvent<HTMLTextAreaElement | HTMLDivElement>,
  ) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === "Escape") {
      e.preventDefault();
      setEditing(false);
    } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submitEdit();
    }
  };

  if (editing) {
    return (
      <div
        className="flex flex-col items-end gap-2"
        data-testid="user-message"
        data-copy-plain=""
      >
        <div className="w-full max-w-[80%] rounded-xl rounded-br-none border border-border bg-card p-2">
          {marked ? (
            <UserInlineDraft
              ref={draftRef}
              value={draft}
              attachments={attachments}
              mentions={agentMentions}
              conversationId={conversationId}
              onChange={setDraft}
              onKeyDown={onEditKeyDown}
            />
          ) : (
            <textarea
              ref={editRef}
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                e.target.style.height = "0";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 240)}px`;
              }}
              onKeyDown={onEditKeyDown}
              className="w-full resize-none bg-transparent px-2 py-1 text-sm text-foreground focus:outline-none"
              rows={1}
            />
          )}
          <div className="flex items-center justify-end gap-1.5 pt-1">
            <Button
              variant="neutral"
              icon={<X size={13} />}
              onClick={() => setEditing(false)}
            >
              取消
            </Button>
            <Button
              icon={<Check size={13} />}
              onClick={submitEdit}
              disabled={!draft.trim()}
            >
              发送
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="group flex flex-col items-end gap-1.5"
      data-testid="user-message"
      data-copy-plain=""
    >
      {!hasInlineMarkers(message.content) && (
        <UserChipTray
          attachments={attachments}
          mentions={agentMentions}
          conversationId={conversationId}
        />
      )}
      {queuedHere && (
        <p
          className="text-xs text-muted-foreground"
          data-testid="user-message-queued"
        >
          排队中
        </p>
      )}
      <div className="max-w-[80%] rounded-xl rounded-br-none bg-muted px-4 py-3 text-sm text-foreground">
        <CollapsibleSpeech
          contentKey={message.content}
          fadeToClass="from-muted"
          collapsedMaxH={USER_BUBBLE_COLLAPSED_MAX_H}
          sceneKey={`user:${message.id}`}
        >
          {hasInlineMarkers(message.content) ? (
            <UserInlineBody
              content={message.content}
              attachments={attachments}
              mentions={agentMentions}
              conversationId={conversationId}
            />
          ) : (
            <p className="whitespace-pre-wrap">{message.content}</p>
          )}
        </CollapsibleSpeech>
      </div>
      {message.syncStatus && (
        <div className="flex justify-end">
          <SyncStatusHint syncStatus={message.syncStatus} align="end" />
        </div>
      )}
      {!isGenerating && (
        <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <MessageAction
            icon={copied ? <Check size={13} /> : <Copy size={13} />}
            label={copied ? "已复制" : "复制"}
            onClick={onCopy}
          />
          <MessageAction
            icon={<Pencil size={13} />}
            label="编辑"
            onClick={startEdit}
          />
          <MessageTime iso={message.createdAt} />
        </div>
      )}
    </div>
  );
}
