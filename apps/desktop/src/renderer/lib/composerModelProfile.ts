import { useConversations } from "@/hooks/useConversations";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import {
  type LlmModelProfileView,
  resolveDefaultProfile,
} from "@/services/llmModelProfiles";
import { isImageAttachment } from "@/services/messaging";
import {
  type ModelCatalogItem,
  getLastUsedProfileId,
  slotHasCatalogVision,
} from "@/services/models";
import { useConversationStore } from "@/stores/conversation";
import { create } from "zustand";

/**
 * New-chat profile pick (ModelPicker local draft). Last-used is written on pick;
 * this store lets the composer vision hint subscribe to the same choice.
 */
type ComposerProfileDraftState = {
  profileId: string | null;
  setProfileId: (profileId: string | null) => void;
};

export const useComposerProfileDraftStore = create<ComposerProfileDraftState>(
  (set) => ({
    profileId: null,
    setProfileId: (profileId) => set({ profileId }),
  }),
);

/** Session / new-chat profile id: conversation pin → draft pick → last-used. */
export function resolveComposerProfileId(args: {
  conversationId: string | null;
  conversationProfileId: string | null | undefined;
  draftProfileId: string | null | undefined;
  lastUsedProfileId: string | null;
  profileIds: readonly string[];
}): string | null {
  const overrideId = args.conversationProfileId?.trim() || null;
  if (overrideId) return overrideId;
  if (args.conversationId) return null;
  const draft = args.draftProfileId?.trim() || null;
  if (draft) return draft;
  const last = args.lastUsedProfileId?.trim() || null;
  if (last && args.profileIds.includes(last)) return last;
  return null;
}

export function lookupComposerProfile(
  selectedId: string | null,
  profiles: readonly LlmModelProfileView[],
  accountDefault: LlmModelProfileView | undefined,
): LlmModelProfileView | undefined {
  if (selectedId) {
    return profiles.find((p) => p.id === selectedId) ?? accountDefault;
  }
  return accountDefault;
}

/** Same resolution as ModelPicker (session pin / new-chat draft / last-used / default). */
export function useComposerActiveProfile(): LlmModelProfileView | undefined {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const conversations = useConversations();
  const { data: profileList } = useLlmModelProfiles();
  const draftProfileId = useComposerProfileDraftStore((s) => s.profileId);
  const profiles = profileList?.data ?? [];
  const accountDefault = resolveDefaultProfile(profileList);
  const activeConv = conversationId
    ? conversations.find((c) => c.id === conversationId)
    : undefined;
  const selectedId = resolveComposerProfileId({
    conversationId,
    conversationProfileId: activeConv?.modelProfileId,
    draftProfileId,
    lastUsedProfileId: getLastUsedProfileId(),
    profileIds: profiles.map((p) => p.id),
  });
  return lookupComposerProfile(selectedId, profiles, accountDefault);
}

export function profileCanSeeImages(
  profile: LlmModelProfileView | undefined,
  catalogModels: ModelCatalogItem[],
): boolean {
  if (!profile) return false;
  if (profile.vision?.model?.trim()) return true;
  const main = profile.main;
  if (!main?.model) return false;
  return slotHasCatalogVision(main, catalogModels);
}

export function draftHasImageAttachment(
  attachments: ReadonlyArray<{ name: string }>,
): boolean {
  return attachments.some((a) => isImageAttachment(a.name));
}

export function shouldShowComposerVisionHint(opts: {
  hasImage: boolean;
  profile: LlmModelProfileView | undefined;
  catalogModels: ModelCatalogItem[];
}): boolean {
  if (!opts.hasImage) return false;
  if (!opts.profile) return false;
  if (opts.profile.vision?.model?.trim()) return false;
  // Catalog still loading: don't flash「不能看图」on a VL main.
  if (opts.catalogModels.length === 0) return false;
  return !profileCanSeeImages(opts.profile, opts.catalogModels);
}

/** Pre-send muted line; does not block send. */
export const COMPOSER_VISION_HINT = "当前组合不能看图，也未配置识图";
