import { useModels } from "@/hooks/useModels";
import { useChatPaneId } from "@/lib/chatPane";
import {
  COMPOSER_VISION_DECLARE,
  COMPOSER_VISION_HINT,
  byokVisionDeclareRef,
  draftHasImageAttachment,
  shouldOfferComposerVisionDeclare,
  shouldShowComposerVisionHint,
  useComposerActiveProfile,
} from "@/lib/composerModelProfile";
import { queryClient } from "@/lib/queryClient";
import { modelKeys } from "@/lib/queryKeys";
import { putVisionAccept } from "@/services/models";
import { draftKeyFor, useComposerDraftStore } from "@/stores/composer";
import { useState } from "react";

const EMPTY_ATTACHMENTS: { name: string }[] = [];

/**
 * Draft has an image and the current combo cannot see it (no catalog vision on
 * main). Muted pre-send line; does not block send. BYOK mains can declare.
 */
export function ComposerVisionHint() {
  const conversationId = useChatPaneId();
  const draftKey = draftKeyFor(conversationId);
  const attachments = useComposerDraftStore(
    (s) => s.drafts[draftKey]?.attachments ?? EMPTY_ATTACHMENTS,
  );
  const { data: catalog } = useModels();
  const profile = useComposerActiveProfile();
  const [pending, setPending] = useState(false);
  const catalogModels = catalog?.models ?? [];
  const hintOpts = {
    hasImage: draftHasImageAttachment(attachments),
    profile,
    catalogModels,
  };
  if (!shouldShowComposerVisionHint(hintOpts)) {
    return null;
  }
  const declareRef = shouldOfferComposerVisionDeclare(hintOpts)
    ? byokVisionDeclareRef(profile)
    : null;

  async function rememberVision() {
    if (!declareRef || pending) return;
    setPending(true);
    try {
      await putVisionAccept({ ref: declareRef, accept: true });
      await queryClient.invalidateQueries({ queryKey: modelKeys.catalog });
    } catch {
      // Hint stays; this send still honest-notes until the declare lands.
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      aria-live="polite"
      data-testid="composer-vision-hint"
      className="flex items-center gap-1.5 px-4 pt-2 text-xs text-muted-foreground"
    >
      <span>{COMPOSER_VISION_HINT}</span>
      {declareRef ? (
        <button
          type="button"
          data-testid="composer-vision-declare"
          className="font-medium text-primary hover:underline disabled:opacity-40"
          disabled={pending}
          onClick={() => {
            void rememberVision();
          }}
        >
          {COMPOSER_VISION_DECLARE}
        </button>
      ) : null}
    </div>
  );
}
