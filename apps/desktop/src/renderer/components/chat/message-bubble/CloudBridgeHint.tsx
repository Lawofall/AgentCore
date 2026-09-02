import {
  CLOUD_BRIDGE_HINT,
  shouldShowCloudBridgeHint,
} from "@/lib/cloudBridgeHint";
import {
  DRAFT_KEY,
  lastAssistantMessageId,
  useConversationStore,
} from "@/stores/conversation";
import { useUIStore } from "@/stores/ui";

/**
 * Latest-assistant footnote when this local-bound chat's last turn ran via cloud.
 * Session-level ``executionVia`` — same lifetime as the old composer line
 * (clears when the next turn returns to sidecar). Not a path switcher.
 */
export function CloudBridgeHint({ messageId }: { messageId: string }) {
  const sidecarPreference = useUIStore((s) => s.sidecarPreference);
  const via = useConversationStore(
    (s) => s.byId?.[s.currentConversationId ?? DRAFT_KEY]?.executionVia ?? null,
  );
  const lastAssistantId = useConversationStore((s) =>
    lastAssistantMessageId(
      s.byId?.[s.currentConversationId ?? DRAFT_KEY]?.messages ?? [],
    ),
  );
  const isStreaming = useConversationStore((s) => {
    const msgs = s.byId?.[s.currentConversationId ?? DRAFT_KEY]?.messages ?? [];
    return msgs.find((m) => m.id === messageId)?.isStreaming === true;
  });
  if (
    !shouldShowCloudBridgeHint({
      via,
      sidecarPreference,
      isLatestAssistant: lastAssistantId === messageId,
      isStreaming,
    })
  ) {
    return null;
  }
  return (
    <p
      data-testid="cloud-bridge-hint"
      aria-live="polite"
      className="mt-1 text-xs text-muted-foreground"
    >
      {CLOUD_BRIDGE_HINT}
    </p>
  );
}
