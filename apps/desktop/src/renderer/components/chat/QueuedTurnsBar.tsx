import { renderInlineLabels } from "@/lib/inlineBody";
import { notifyError } from "@/lib/toast";
import {
  cancelQueuedTurn,
  steerQueuedTurn,
} from "@/services/turns/cancelQueuedTurn";
import { type QueuedTurnEntry, useQueuedTurns } from "@/stores/queuedTurns";
import { Loader2, X } from "lucide-react";
import { useState } from "react";

/**
 * 排队条：drain 前可按项取消或立刻插队（Stop ≠ 取消排队）。
 * 挂在 composer 上方；正文在主时间线用户泡（ack 即入场），条不是唯一载体。
 * 本端发送 ack 即时 upsert；快照对账兜底。
 */
export function QueuedTurnsBar({
  conversationId,
}: {
  conversationId: string | null;
}) {
  const items = useQueuedTurns(conversationId);
  if (!conversationId || items.length === 0) return null;

  return (
    <div
      className="flex flex-col gap-1 px-1 pb-1"
      data-testid="queued-turns-bar"
      aria-live="polite"
      aria-label={`已排队 ${items.length} 条`}
    >
      {items.length > 1 && (
        <div className="px-2 text-xs text-muted-foreground">
          已排队 {items.length} 条
        </div>
      )}
      {items.map((item) => (
        <QueuedTurnRow key={item.queueId} item={item} />
      ))}
    </div>
  );
}

function QueuedTurnRow({ item }: { item: QueuedTurnEntry }) {
  const [busy, setBusy] = useState(false);
  const previewText = renderInlineLabels(
    item.content,
    item.attachments ?? [],
    item.agentMentions ?? [],
  );
  const preview =
    previewText.length > 48 ? `${previewText.slice(0, 48)}…` : previewText;
  const fromInterjection = Boolean(item.interjectionId);

  const onCancel = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await cancelQueuedTurn(item.conversationId, item.queueId);
      // 成功 / 404 已在 cancelQueuedTurn 内本地清条。
    } catch (err) {
      notifyError(err, "取消排队失败");
    } finally {
      setBusy(false);
    }
  };

  const onSteer = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await steerQueuedTurn(item.conversationId, item.queueId);
      // toast：steer ack / 降级排队由 SSE → queuedNotify；勿本地伪装「已插入」。
    } catch (err) {
      notifyError(err, "插队失败");
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
    >
      <Loader2 size={12} className="shrink-0 animate-spin" aria-hidden />
      <span className="min-w-0 flex-1 truncate">
        排队中
        {item.queueDepth > 1
          ? `（第 ${item.position}/${item.queueDepth}）`
          : ""}
        {fromInterjection ? " · 来自你的插话" : ""}：{preview}
      </span>
      <button
        type="button"
        className="shrink-0 rounded-lg px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        aria-label="立刻插队"
        title="取消排队并以插队重发；下一工具步生效，不会立刻打断当前输出"
        disabled={busy}
        data-testid="queued-turn-steer"
        onClick={() => void onSteer()}
      >
        立刻插队
      </button>
      <button
        type="button"
        className="shrink-0 rounded-lg p-0.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        aria-label="取消排队"
        title="取消排队"
        disabled={busy}
        data-testid="queued-turn-cancel"
        onClick={() => void onCancel()}
      >
        <X size={12} />
      </button>
    </div>
  );
}
