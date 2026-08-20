import { Button, IconButton } from "@/components/ui";
import {
  noticeChipNeutral,
  statusAccentText,
  statusChip,
} from "@/components/ui/tone-presets";
import { cn } from "@/lib/utils";
import { isReconnectQuietBanner } from "@/services/turns/helpers";
import {
  useActiveError,
  useActiveErrorAction,
  useConversationStore,
} from "@/stores/conversation";
import { AlertTriangle, Info, KeyRound, X } from "lucide-react";
import { useNavigate } from "react-router-dom";

/**
 * Banner for a failed turn (send / regenerate transport error). Chat surfaces
 * the same copy on {@link import("./message-input/TurnComposer").TurnComposer}
 * (empty-state first-send included). Displays the error copy; the optional
 * action routes the user to fix the cause (e.g. "去配置" → model config for a
 * missing BYOK key); dismissing only hides the banner.
 *
 * Tone: config remedy (去配置) = blue `primary`; quiet reconnect / finished
 * copy uses Info on {@link noticeChipNeutral}; confirmed-bad reconnect and
 * other recoverable interruptions keep the triangle (not danger red).
 *
 * Conversation-scoped (reads the active conversation's error state) and therefore
 * self-contained wherever it mounts — mirrors {@link import("./ApprovalPrompt").ApprovalPrompt}
 * / {@link import("./ResumePrompt").ResumePrompt}.
 */
export function RetryBanner() {
  const error = useActiveError();
  const action = useActiveErrorAction();
  const clearError = useConversationStore((s) => s.clearError);
  const navigate = useNavigate();
  if (!error) return null;

  const needsYou = Boolean(action);
  const quiet = !needsYou && isReconnectQuietBanner(error);
  const Icon = quiet ? Info : AlertTriangle;

  return (
    <div
      data-banner-tone={needsYou ? "primary" : quiet ? "notice" : "alert"}
      className={cn(
        "mx-4 mb-2 flex items-center gap-2 rounded-lg border px-3 py-2 text-sm",
        needsYou ? statusChip.primary : noticeChipNeutral,
      )}
    >
      <Icon
        size={15}
        className={cn(
          "shrink-0",
          needsYou ? statusAccentText.primary : "text-muted-foreground",
        )}
      />
      <span className="min-w-0 flex-1">{error}</span>
      {action && (
        <Button
          variant="primary"
          className="shrink-0"
          icon={<KeyRound size={13} />}
          onClick={() => {
            clearError();
            navigate(action.href);
          }}
        >
          {action.label}
        </Button>
      )}
      <IconButton
        onClick={() => clearError()}
        aria-label="关闭"
        className={
          needsYou
            ? "text-primary/70 hover:bg-transparent hover:text-primary"
            : "text-muted-foreground hover:bg-transparent hover:text-foreground"
        }
      >
        <X size={14} />
      </IconButton>
    </div>
  );
}
