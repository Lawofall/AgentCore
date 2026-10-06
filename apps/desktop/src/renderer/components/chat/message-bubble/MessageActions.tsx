import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useDuplicateConversation } from "@/hooks/useConversations";
import { useChatPaneId } from "@/lib/chatPane";
import { formatMessageTime } from "@/lib/format";
import { notifyError } from "@/lib/toast";
import { useConversationStore } from "@/stores/conversation";
import { Check, GitFork, RefreshCw, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Assistant footer regenerate — always confirm (定案：截断其后历史 + 新贵回合，不可逆).
 * Does not cover edit-and-resend on the user bubble (intentional edit path).
 */
export function RegenerateMessageAction({
  onRegenerate,
}: { onRegenerate: () => void }) {
  const [confirming, setConfirming] = useState(false);

  if (confirming) {
    return (
      <span className="inline-flex items-center gap-0.5">
        <SimpleTooltip label="确认重新生成">
          <IconButton
            size="sm"
            tone="destructive"
            aria-label="确认重新生成"
            onClick={() => {
              setConfirming(false);
              onRegenerate();
            }}
          >
            <Check size={14} />
          </IconButton>
        </SimpleTooltip>
        <SimpleTooltip label="取消">
          <IconButton
            size="sm"
            aria-label="取消"
            onClick={() => setConfirming(false)}
          >
            <X size={14} />
          </IconButton>
        </SimpleTooltip>
      </span>
    );
  }

  return (
    <SimpleTooltip label="重新生成">
      <IconButton
        size="sm"
        aria-label="重新生成"
        onClick={() => setConfirming(true)}
      >
        <RefreshCw size={14} />
      </IconButton>
    </SimpleTooltip>
  );
}

/**
 * Assistant footer clone — copy through this message into a new conversation.
 * Non-destructive (original unchanged); cutoff is this bubble.
 */
export function CloneMessageAction({ messageId }: { messageId: string }) {
  const conversationId = useChatPaneId();
  const switchConversation = useConversationStore((s) => s.switchConversation);
  const navigate = useNavigate();
  const duplicateMutation = useDuplicateConversation();

  if (!conversationId) return null;

  return (
    <SimpleTooltip label="复制到这条为止，原对话不动">
      <IconButton
        size="sm"
        aria-label="克隆对话"
        disabled={duplicateMutation.isPending}
        onClick={() => {
          duplicateMutation.mutate(
            { id: conversationId, untilMessageId: messageId },
            {
              onSuccess: (conv) => {
                switchConversation(conv.id);
                navigate(`/conversations/${conv.id}`);
              },
              onError: (err) => notifyError(err, "克隆失败"),
            },
          );
        }}
      >
        <GitFork size={14} />
      </IconButton>
    </SimpleTooltip>
  );
}

export function MessageTime({ iso }: { iso: string }) {
  const label = formatMessageTime(iso);
  if (!label) return null;
  return (
    <SimpleTooltip label={new Date(iso).toLocaleString()}>
      <span className="ml-1 cursor-default text-xs text-muted-foreground/60">
        {label}
      </span>
    </SimpleTooltip>
  );
}
