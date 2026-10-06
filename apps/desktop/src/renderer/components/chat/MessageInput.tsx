import { useChatPaneId } from "@/lib/chatPane";
import { QueuedTurnsBar } from "./QueuedTurnsBar";
import { SteerWaitingBar } from "./SteerWaitingBar";
import {
  TurnComposer,
  type TurnComposerVariant,
} from "./message-input/TurnComposer";

/**
 * The chat view's composer: the unified {@link TurnComposer} in its chat chrome
 * (bottom padding, default placeholder). One composer, single draft per conversation;
 * canvas is look-only — say stays here.
 *
 * `variant` is chosen by ChatView: `bar` for the session bottom dock（＋收纳配置）,
 * `card` for the centered new-chat composer.
 *
 * Workspace / Git / compose actions live inside TurnComposer. When fused under
 * ApprovalPrompt, ChatView stacks ApprovalPrompt above this input so the
 * 一体圆角不受打断.
 */
export function MessageInput({
  className,
  variant = "card",
  attachedBelowApproval = false,
}: {
  className?: string;
  variant?: TurnComposerVariant;
  /** Flush under ApprovalPrompt in the bottom-bar 一体态. */
  attachedBelowApproval?: boolean;
}) {
  const conversationId = useChatPaneId();
  return (
    <div className={className ?? "px-4 pb-4 pt-2"}>
      <SteerWaitingBar conversationId={conversationId} />
      <QueuedTurnsBar conversationId={conversationId} />
      <TurnComposer
        variant={variant}
        attachedBelowApproval={attachedBelowApproval}
      />
    </div>
  );
}
