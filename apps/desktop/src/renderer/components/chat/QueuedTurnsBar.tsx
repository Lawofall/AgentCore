import { useLiveCoordinatingTurn } from "@/lib/composerDelivery";
import { renderInlineLabels } from "@/lib/inlineBody";
import { notifyError } from "@/lib/toast";
import {
  cancelQueuedTurn,
  deliverQueuedTurnToCaptain,
  reorderQueuedTurns,
} from "@/services/turns/cancelQueuedTurn";
import { type QueuedTurnEntry, useQueuedTurns } from "@/stores/queuedTurns";
import { GripVertical, Loader2, X } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import {
  QueuedTurnEditor,
  pendingAttachments,
  pendingMentions,
} from "./QueuedTurnEditor";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "./message-input/composerAttachments";

interface QueueEditDraft {
  content: string;
  attachments: PendingAttachment[];
  mentions: PendingAgentMention[];
}

/**
 * 排队挂件：生成中再发先挂在输入框上方。
 * 点行编辑正文、附件和 @。团队还在时可以「送给主管」。取消仍在。
 * 两条以上可拖动改顺序。整轮停在输入框「停止生成」，停完队首那句自己开跑。
 * 时间线用户泡等到出队再出现，所以单条也画，不跟气泡重复一套按钮。
 */
export function QueuedTurnsBar({
  conversationId,
}: {
  conversationId: string | null;
}) {
  const items = useQueuedTurns(conversationId);
  const teamLive = useLiveCoordinatingTurn();
  const [session, setSession] = useState<{
    queueId: string;
    detached: boolean;
  } | null>(null);
  const draftRef = useRef<QueueEditDraft | null>(null);
  const discard = useCallback(() => {
    draftRef.current = null;
    setSession(null);
  }, []);
  const live = session
    ? items.find((item) => item.queueId === session.queueId)
    : undefined;
  const orphan = Boolean(session && (session.detached || !live));
  if (!conversationId || (items.length === 0 && !session)) return null;

  const onDropOn = (targetId: string, draggedId: string) => {
    if (!draggedId || draggedId === targetId) return;
    const ids = items.map((item) => item.queueId);
    const without = ids.filter((id) => id !== draggedId);
    const index = without.indexOf(targetId);
    if (index < 0) return;
    without.splice(index, 0, draggedId);
    void reorderQueuedTurns(conversationId, without).catch((err) => {
      notifyError(err, "调整排队顺序失败");
    });
  };

  const openEdit = (item: QueuedTurnEntry) => {
    draftRef.current = {
      content: item.content,
      attachments: pendingAttachments(item.attachments ?? []),
      mentions: pendingMentions(item.agentMentions ?? []),
    };
    setSession({ queueId: item.queueId, detached: false });
  };

  const draft = draftRef.current;
  const editor =
    session && draft ? (
      <QueuedTurnEditor
        key={session.queueId}
        conversationId={conversationId}
        queueId={session.queueId}
        initialContent={draft.content}
        initialAttachments={draft.attachments}
        initialMentions={draft.mentions}
        detached={session.detached || !live}
        draftRef={draftRef}
        onDiscard={discard}
        onSaved={discard}
        onDetached={() =>
          setSession((current) =>
            current ? { ...current, detached: true } : current,
          )
        }
      />
    ) : null;

  return (
    <div
      className="flex flex-col gap-1 px-1 pb-1"
      data-testid="queued-turns-bar"
      aria-live="polite"
      aria-label={`已排队 ${items.length} 条`}
    >
      {orphan ? editor : null}
      {items.length > 1 && (
        <div className="px-2 text-xs text-muted-foreground">
          已排队 {items.length} 条
        </div>
      )}
      {items.map((item) => {
        if (session?.queueId === item.queueId) {
          if (orphan) return null;
          return <div key={item.queueId}>{editor}</div>;
        }
        return (
          <QueuedTurnRow
            key={item.queueId}
            item={item}
            canDrag={items.length > 1 && !session}
            toCaptain={teamLive}
            onDropOn={onDropOn}
            onEdit={() => openEdit(item)}
          />
        );
      })}
    </div>
  );
}

function QueuedTurnRow({
  item,
  canDrag,
  toCaptain,
  onDropOn,
  onEdit,
}: {
  item: QueuedTurnEntry;
  canDrag: boolean;
  toCaptain: boolean;
  onDropOn: (targetId: string, draggedId: string) => void;
  onEdit: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const previewText = renderInlineLabels(
    item.content,
    item.attachments ?? [],
    item.agentMentions ?? [],
  );
  const preview =
    previewText.length > 48 ? `${previewText.slice(0, 48)}…` : previewText;
  const fromInterjection = Boolean(item.interjectionId);

  const run = async (action: () => Promise<unknown>, failure: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await action();
    } catch (err) {
      notifyError(err, failure);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground"
      data-testid="queued-turn-row"
      data-queue-id={item.queueId}
      data-from-interjection={fromInterjection ? "true" : undefined}
      onDragOver={
        canDrag
          ? (event) => {
              event.preventDefault();
            }
          : undefined
      }
      onDrop={
        canDrag
          ? (event) => {
              event.preventDefault();
              const draggedId = event.dataTransfer.getData("text/plain");
              onDropOn(item.queueId, draggedId);
            }
          : undefined
      }
    >
      {canDrag ? (
        <span
          draggable
          className="shrink-0 cursor-grab text-muted-foreground"
          aria-label="拖动调整顺序"
          data-testid="queued-turn-drag"
          onDragStart={(event) => {
            event.dataTransfer.setData("text/plain", item.queueId);
            event.dataTransfer.effectAllowed = "move";
          }}
        >
          <GripVertical size={12} aria-hidden />
        </span>
      ) : (
        <Loader2 size={12} className="shrink-0 animate-spin" aria-hidden />
      )}
      <button
        type="button"
        className="min-w-0 flex-1 truncate text-left text-xs text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        data-testid="queued-turn-edit"
        aria-label={`编辑排队：${preview}`}
        onClick={onEdit}
      >
        排队中
        {fromInterjection ? " · 来自你的插话" : ""}：{preview}
      </button>
      {toCaptain ? (
        <button
          type="button"
          className="shrink-0 rounded-lg px-1.5 py-0.5 text-xs text-foreground hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          aria-label="送给主管"
          title="送给主管，团队继续做。主管下一拍读到，不会自动改派"
          disabled={busy}
          data-testid="queued-turn-to-captain"
          onClick={() =>
            void run(async () => {
              const outcome = await deliverQueuedTurnToCaptain(
                item.conversationId,
                item.queueId,
              );
              if (outcome === "no_captain") {
                notifyError("团队已经不在，这句话还在排队", "送给主管失败");
              }
            }, "送给主管失败")
          }
        >
          送给主管
        </button>
      ) : null}
      <button
        type="button"
        className="shrink-0 rounded-lg p-0.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        aria-label="取消排队"
        title="取消排队"
        disabled={busy}
        data-testid="queued-turn-cancel"
        onClick={() =>
          void run(
            () => cancelQueuedTurn(item.conversationId, item.queueId),
            "取消排队失败",
          )
        }
      >
        <X size={12} />
      </button>
    </div>
  );
}
