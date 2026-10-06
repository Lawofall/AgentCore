import {
  contextCutAvailable,
  patchContextCutCache,
  useCutConversationRows,
} from "@/components/chat/contextCut";
import { Button, IconButton, Textarea } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useChatPaneId, useChatPaneSliceKey } from "@/lib/chatPane";
import { ApiError } from "@/services/api";
import {
  type ContextCutPreview,
  commitContextCut,
  previewContextCut,
} from "@/services/conversations";
import {
  type Message,
  lastAssistantProjectionId,
  runtimeOf,
  useConversationStore,
} from "@/stores/conversation";
import { conversationStillWriting } from "@/stores/conversation/selectors";
import {
  execRuntime,
  isConversationExecutionLive,
  useExecutionStore,
} from "@/stores/execution";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import { FoldVertical } from "lucide-react";
import { useEffect, useState } from "react";

const NO_MESSAGES: Message[] = [];

function cutDecision(foldedCount: number, keepUserLine: boolean): string {
  const lead = `收起更早的 ${foldedCount} 条，换成下面这份摘要`;
  if (keepUserLine) {
    return `${lead}，引出这条回复的那句话也留下，屏幕上仍可上翻。`;
  }
  return `${lead}，屏幕上仍可上翻。`;
}

function cutMessageId(message: Message): string {
  return message.serverMessageId || message.id;
}

function errorText(err: unknown): string {
  if (err instanceof ApiError && err.serverMessage) return err.serverMessage;
  if (err instanceof Error && err.message) return err.message;
  return "没能收成摘要，更早的内容还在";
}

/**
 * 「从此按这条继续」— retire exploration before this message.
 * Hidden when the window has nothing earlier to fold, or this desk is busy.
 */
export function ContextCutAction({ message }: { message: Message }) {
  const conversationId = useChatPaneId();
  const paneKey = useChatPaneSliceKey();
  const messages = useConversationStore(
    (s) => runtimeOf(s, paneKey).messages ?? NO_MESSAGES,
  );
  const hasMoreBefore = useConversationStore(
    (s) => runtimeOf(s, paneKey).hasMoreBefore,
  );
  const writing = useConversationStore((s) =>
    conversationStillWriting(runtimeOf(s, paneKey)),
  );
  const paused = usePausedTurnStore((s) =>
    conversationId
      ? s.pending.some((row) => row.conversationId === conversationId)
      : false,
  );
  const lastKey = lastAssistantProjectionId(messages);
  const teamLive = useExecutionStore((s) =>
    lastKey ? isConversationExecutionLive(execRuntime(s, lastKey)) : false,
  );
  const toolRunning = messages.some((row) =>
    (row.process ?? []).some(
      (step) => step.kind === "tool" && step.status === "running",
    ),
  );
  const rows = useCutConversationRows();
  const compactedThrough = conversationId
    ? (rows.find((row) => row.id === conversationId)?.compactedThrough ?? null)
    : null;
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<ContextCutPreview | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);

  const messageId = cutMessageId(message);
  const available = contextCutAvailable({
    message,
    messages,
    compactedThrough,
    hasMoreBefore,
    blocked: writing || paused || teamLive || toolRunning,
  });

  useEffect(() => {
    if (!open || !conversationId) return;
    let cancelled = false;
    setLoading(true);
    setPreview(null);
    setDraft("");
    setError(null);
    void previewContextCut(conversationId, messageId)
      .then((next) => {
        if (!cancelled) {
          setPreview(next);
          setDraft(next.summary);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, conversationId, messageId]);

  if (!available || !conversationId) return null;

  const keepDiffers = preview != null && preview.keepMessageId !== messageId;

  const prose = draft.trim();
  const confirm = () => {
    if (!preview || !prose) return;
    setCommitting(true);
    void commitContextCut(conversationId, messageId, preview.foldDigest, prose)
      .then((conv) => {
        patchContextCutCache(conv.id, {
          contextCompacted: conv.contextCompacted,
          compactedThrough: conv.compactedThrough,
          contextCutUndoable: conv.contextCutUndoable ?? false,
        });
        setOpen(false);
      })
      .catch((err: unknown) => {
        setError(errorText(err));
      })
      .finally(() => setCommitting(false));
  };

  return (
    <>
      <SimpleTooltip label="从此按这条继续">
        <IconButton
          size="sm"
          aria-label="从此按这条继续"
          onClick={() => setOpen(true)}
        >
          <FoldVertical size={14} />
        </IconButton>
      </SimpleTooltip>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="lg" className="flex max-h-[80vh] flex-col">
          <DialogHeader className="shrink-0">
            <DialogTitle>按这条继续</DialogTitle>
            <DialogDescription>
              {preview
                ? cutDecision(preview.foldedCount, keepDiffers)
                : (error ?? "正在收成摘要…")}
            </DialogDescription>
          </DialogHeader>
          {preview ? (
            <DialogBody className="flex flex-col gap-2 overflow-y-hidden pb-3">
              {error ? (
                <p className="text-sm text-foreground">{error}</p>
              ) : null}
              <label
                className="text-xs text-muted-foreground"
                htmlFor="context-cut-summary"
              >
                摘要
              </label>
              <Textarea
                id="context-cut-summary"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                className="min-h-32 max-h-[min(50vh,24rem)] w-full"
              />
            </DialogBody>
          ) : null}
          <DialogFooter className="shrink-0">
            <Button
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={committing}
            >
              取消
            </Button>
            <Button
              onClick={confirm}
              disabled={!preview || !prose || committing || loading}
            >
              就按这个继续
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
