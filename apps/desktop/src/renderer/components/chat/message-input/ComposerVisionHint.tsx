import { useModels } from "@/hooks/useModels";
import {
  COMPOSER_VISION_HINT,
  draftHasImageAttachment,
  shouldShowComposerVisionHint,
  useComposerActiveProfile,
} from "@/lib/composerModelProfile";
import { draftKeyFor, useComposerDraftStore } from "@/stores/composer";
import { useConversationStore } from "@/stores/conversation";

const EMPTY_ATTACHMENTS: { name: string }[] = [];

/**
 * Draft has an image and the current combo cannot see it (no catalog vision on
 * main, no vision slot). Muted pre-send line; does not block send.
 */
export function ComposerVisionHint() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const draftKey = draftKeyFor(conversationId);
  const attachments = useComposerDraftStore(
    (s) => s.drafts[draftKey]?.attachments ?? EMPTY_ATTACHMENTS,
  );
  const { data: catalog } = useModels();
  const profile = useComposerActiveProfile();
  if (
    !shouldShowComposerVisionHint({
      hasImage: draftHasImageAttachment(attachments),
      profile,
      catalogModels: catalog?.models ?? [],
    })
  ) {
    return null;
  }
  return (
    <div
      aria-live="polite"
      data-testid="composer-vision-hint"
      className="flex items-center gap-1.5 px-4 pt-2 text-xs text-muted-foreground"
    >
      {COMPOSER_VISION_HINT}
    </div>
  );
}
