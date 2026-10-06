import {
  patchContextCutCache,
  useCutConversationRows,
} from "@/components/chat/contextCut";
import { Button } from "@/components/ui";
import { useChatPaneId } from "@/lib/chatPane";
import { notifyError } from "@/lib/toast";
import { undoContextCut } from "@/services/conversations";
import { useState } from "react";

/** Centered ghost line at the rolling-compaction fold (权限切换同款，无卡、无摘要正文). */
export const CONTEXT_COMPACTED_DIVIDER_HINT =
  "以上内容已收入摘要，原文仍可上翻";

export function CompactionDivider() {
  const conversationId = useChatPaneId();
  const rows = useCutConversationRows();
  const undoable = conversationId
    ? Boolean(rows.find((row) => row.id === conversationId)?.contextCutUndoable)
    : false;
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);

  const undo = () => {
    if (!conversationId || pending) return;
    setPending(true);
    void undoContextCut(conversationId)
      .then((conv) => {
        patchContextCutCache(conv.id, {
          contextCompacted: conv.contextCompacted,
          compactedThrough: conv.compactedThrough,
          contextCutUndoable: conv.contextCutUndoable ?? false,
        });
        setConfirming(false);
      })
      .catch((err: unknown) => {
        notifyError(err, "撤回失败");
        setConfirming(false);
      })
      .finally(() => setPending(false));
  };

  return (
    <div
      data-testid="context-compacted-divider"
      className="flex items-center gap-2 text-xs text-muted-foreground"
    >
      <span className="h-px flex-1 bg-border" />
      <span className="inline-flex shrink-0 items-center gap-1 px-1 text-center">
        {CONTEXT_COMPACTED_DIVIDER_HINT}
        {undoable && !confirming ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={pending}
            onClick={() => setConfirming(true)}
          >
            撤回
          </Button>
        ) : null}
        {undoable && confirming ? (
          <>
            <Button variant="ghost" size="sm" disabled={pending} onClick={undo}>
              确认撤回
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={pending}
              onClick={() => setConfirming(false)}
            >
              取消
            </Button>
          </>
        ) : null}
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
