import {
  CollapsibleSpeech,
  USER_BUBBLE_COLLAPSED_MAX_H,
} from "@/components/chat/debate/CollapsibleSpeech";
import {
  INTERJECTION_TONE_CLASS,
  interjectionStatusLabel,
  interjectionStatusTone,
  isInterjectionTurnTerminal,
  showInterjectionStatusChrome,
} from "@/components/chat/interjectionStatus";
import {
  UserChipTray,
  UserInlineBody,
} from "@/components/chat/message-bubble/UserInlineBody";
import { hasInlineMarkers } from "@/lib/inlineBody";
import {
  type MessageAttachmentMeta,
  activeRuntime,
  assistantProjectionId,
  useConversationStore,
} from "@/stores/conversation";
import type { UserInterjection } from "@/stores/execution";
import { useExecutionStore } from "@/stores/execution";

/**
 * 插话主时间线单条（经典 steer + 协调共用）——挂在 process `user_interjection`
 * marker 槽，钉住真实发生位置。
 * 主时间线尚无同内容用户泡时保留完整气泡；已有则折成一行锚点（防双泡）。
 * DURABLE：五态来自 execution.userInterjections（live SSE / journal hydrate）；
 * 正文以主时间线用户泡为准。
 * `addressed` 只留用户泡：徽章与服务端 note 不画（结果已在图/回复）。
 */
export function InterjectionTimeline({
  messageId,
  interjectionId,
}: {
  messageId: string;
  interjectionId: string;
}) {
  const item = useExecutionStore((s) => {
    const list = s.byId[messageId]?.userInterjections;
    if (!list) return null;
    return list.find((i) => i.interjectionId === interjectionId) ?? null;
  });
  const turnTerminal = useConversationStore((s) => {
    const rt = activeRuntime(s);
    const msg = rt.messages.find(
      (m) =>
        m.role === "assistant" &&
        (assistantProjectionId(m) === messageId || m.id === messageId),
    );
    return isInterjectionTurnTerminal(rt.turnPhase, msg?.isStreaming);
  });
  /**
   * 主时间线已有同内容正式 user 消息 → 折叠成锚点（防双泡）。
   * 排队 / 插队发送即入场后，正文以用户泡为准，marker 只留时序。
   */
  const foldContent = item?.content ?? null;
  const folded = useConversationStore((s) => {
    if (!foldContent) return false;
    const messages = activeRuntime(s).messages;
    const assistantIdx = messages.findIndex(
      (m) =>
        m.role === "assistant" &&
        (assistantProjectionId(m) === messageId || m.id === messageId),
    );
    if (assistantIdx < 0) return false;
    for (let i = assistantIdx + 1; i < messages.length; i++) {
      const m = messages[i];
      if (m.role === "user" && m.content === foldContent) return true;
    }
    return false;
  });

  if (!item) return null;
  return folded ? (
    <InterjectionQueuedAnchor item={item} />
  ) : (
    <InterjectionUserBubble item={item} turnTerminal={turnTerminal} />
  );
}

/** 服务端 note：与用户原话视觉分隔并弱化，避免拼成一句。 */
function InterjectionServerNote({ note }: { note: string }) {
  return (
    <p
      className="max-w-[80%] border-t border-border/50 pt-1.5 text-right text-xs text-muted-foreground/70"
      data-testid="interjection-server-note"
    >
      {note}
    </p>
  );
}

/**
 * 同内容用户泡已在主时间线：一行时序注记，正文不重复。
 */
function InterjectionQueuedAnchor({ item }: { item: UserInterjection }) {
  const tone = interjectionStatusTone(item.status);
  return (
    <div
      className="flex items-center justify-end gap-2"
      data-testid={`interjection-note-${item.interjectionId}`}
    >
      <span
        className={`inline-flex shrink-0 rounded-full border px-1.5 py-0.5 text-xs ${INTERJECTION_TONE_CLASS[tone]}`}
        data-testid={`interjection-status-${item.interjectionId}`}
      >
        {interjectionStatusLabel(item.status, { dequeued: true })}
      </span>
      {item.note ? (
        <span
          className="min-w-0 truncate text-xs text-muted-foreground/70"
          title={item.note}
          data-testid="interjection-server-note"
        >
          {item.note}
        </span>
      ) : null}
    </div>
  );
}

function interjectionAtts(item: UserInterjection): MessageAttachmentMeta[] {
  return (item.attachments ?? []).map((a, i) => ({
    id: `${item.interjectionId}:${i}:${a.name}`,
    name: a.name,
    path: a.workspacePath ?? a.name,
    truncated: false,
    workspacePath: a.workspacePath,
  }));
}

function InterjectionUserBubble({
  item,
  turnTerminal,
}: {
  item: UserInterjection;
  turnTerminal: boolean;
}) {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const tone = interjectionStatusTone(item.status);
  const showChrome = showInterjectionStatusChrome(item.status);
  const attachments = interjectionAtts(item);
  const mentions = item.agentMentions ?? [];
  const marked = hasInlineMarkers(item.content);
  return (
    <div
      className="flex flex-col items-end gap-1.5"
      data-testid={`interjection-bubble-${item.interjectionId}`}
    >
      {!marked && (
        <UserChipTray
          attachments={attachments}
          mentions={mentions}
          conversationId={conversationId}
        />
      )}
      <div className="max-w-[80%] rounded-xl rounded-br-none bg-muted px-4 py-3 text-sm text-foreground">
        <CollapsibleSpeech
          contentKey={item.content}
          fadeToClass="from-muted"
          collapsedMaxH={USER_BUBBLE_COLLAPSED_MAX_H}
          sceneKey={`interjection:${item.interjectionId}`}
        >
          {marked ? (
            <UserInlineBody
              content={item.content}
              attachments={attachments}
              mentions={mentions}
              conversationId={conversationId}
            />
          ) : (
            <p className="whitespace-pre-wrap break-words">{item.content}</p>
          )}
        </CollapsibleSpeech>
      </div>
      {showChrome ? (
        <span
          className={`inline-flex max-w-[80%] rounded-full border px-1.5 py-0.5 text-xs ${INTERJECTION_TONE_CLASS[tone]}`}
          data-testid={`interjection-status-${item.interjectionId}`}
        >
          {interjectionStatusLabel(item.status, { turnTerminal })}
        </span>
      ) : null}
      {showChrome && item.note ? (
        <InterjectionServerNote note={item.note} />
      ) : null}
    </div>
  );
}
