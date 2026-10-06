import { getConversations, useConversations } from "@/hooks/useConversations";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import { useChatPaneId } from "@/lib/chatPane";
import { queryClient } from "@/lib/queryClient";
import { llmModelProfileKeys } from "@/lib/queryKeys";
import {
  type LlmModelProfileListResponse,
  type LlmModelProfileView,
  resolveDefaultProfile,
} from "@/services/llmModelProfiles";
import { isImageAttachment } from "@/services/messaging";
import {
  type ModelCatalogItem,
  getLastUsedAssemblyId,
  slotHasCatalogVision,
} from "@/services/models";
import { create } from "zustand";

/**
 * New-chat assembly pick. The model rides on that assembly.
 * Last-used is written on pick; this store lets the vision hint subscribe.
 */
type ComposerProfileDraftState = {
  profileId: string | null;
  assemblyId: string | null;
  setProfileId: (profileId: string | null) => void;
  setAssemblyId: (assemblyId: string | null) => void;
};

export const useComposerProfileDraftStore = create<ComposerProfileDraftState>(
  (set) => ({
    profileId: null,
    assemblyId: null,
    setProfileId: (profileId) => set({ profileId }),
    setAssemblyId: (assemblyId) => set({ assemblyId }),
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

export function lookupComposerProfile<T extends { id: string }>(
  selectedId: string | null,
  profiles: readonly T[],
  accountDefault: T | undefined,
): T | undefined {
  if (selectedId) {
    return profiles.find((p) => p.id === selectedId) ?? accountDefault;
  }
  return accountDefault;
}

/** Session pin / new-chat draft / last-used assembly / account star. */
export function useComposerActiveProfile(): LlmModelProfileView | undefined {
  const conversationId = useChatPaneId();
  const conversations = useConversations();
  const { data: assemblyList } = useLlmModelProfiles();
  const draftAssemblyId = useComposerProfileDraftStore((s) => s.assemblyId);
  const rows = assemblyList?.data ?? [];
  const accountDefault = resolveDefaultProfile(assemblyList);
  const activeConv = conversationId
    ? conversations.find((c) => c.id === conversationId)
    : undefined;
  const selectedId = resolveComposerProfileId({
    conversationId,
    conversationProfileId: activeConv?.assemblyId,
    draftProfileId: draftAssemblyId,
    lastUsedProfileId: getLastUsedAssemblyId(),
    profileIds: rows.map((p) => p.id),
  });
  return lookupComposerProfile(selectedId, rows, accountDefault);
}

/** Assembly context step for a local turn. Null when unset or the list is cold. */
export function contextBudgetForConversation(
  conversationId: string,
): number | null {
  const list = queryClient.getQueryData<LlmModelProfileListResponse>(
    llmModelProfileKeys.list,
  );
  if (!list) return null;
  const rows = list.data ?? [];
  const conv = getConversations().find((row) => row.id === conversationId);
  const selectedId = resolveComposerProfileId({
    conversationId,
    conversationProfileId: conv?.assemblyId,
    draftProfileId: useComposerProfileDraftStore.getState().assemblyId,
    lastUsedProfileId: getLastUsedAssemblyId(),
    profileIds: rows.map((row) => row.id),
  });
  const profile = lookupComposerProfile(
    selectedId,
    rows,
    resolveDefaultProfile(list),
  );
  const budget = profile?.context_budget;
  return typeof budget === "number" && budget > 0 ? budget : null;
}

type VisionProfile = {
  main?: {
    model: string;
    origin: "platform" | "byok";
    provider_id?: string | null;
  } | null;
};

export function profileCanSeeImages(
  profile: VisionProfile | undefined,
  catalogModels: ModelCatalogItem[],
): boolean {
  if (!profile?.main?.model) return false;
  return slotHasCatalogVision(profile.main, catalogModels);
}

export function draftHasImageAttachment(
  attachments: ReadonlyArray<{ name: string }>,
): boolean {
  return attachments.some((a) => isImageAttachment(a.name));
}

export function shouldShowComposerVisionHint(opts: {
  hasImage: boolean;
  profile: VisionProfile | undefined;
  catalogModels: ModelCatalogItem[];
}): boolean {
  if (!opts.hasImage) return false;
  if (!opts.profile) return false;
  // Catalog still loading: don't flash「不能看图」on a VL main.
  if (opts.catalogModels.length === 0) return false;
  return !profileCanSeeImages(opts.profile, opts.catalogModels);
}

/** Pre-send muted line; does not block send. */
export const COMPOSER_VISION_HINT = "当前主模型不能看图";
