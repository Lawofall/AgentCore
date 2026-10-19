import { getConversations, useConversations } from "@/hooks/useConversations";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import { useModels } from "@/hooks/useModels";
import { useChatPaneId } from "@/lib/chatPane";
import { contextBudgetSteps } from "@/lib/contextBudget";
import { pointerValue } from "@/lib/llmDefaults";
import { queryClient } from "@/lib/queryClient";
import { llmModelProfileKeys, modelKeys } from "@/lib/queryKeys";
import { catalogContextLength } from "@/lib/windowFill";
import { api } from "@/services/api";
import {
  type LlmModelProfileListResponse,
  type LlmModelProfileView,
  type ModelProfileSlot,
  resolveDefaultProfile,
} from "@/services/llmModelProfiles";
import { isImageAttachment } from "@/services/messaging";
import {
  type ModelCatalog,
  type ModelCatalogItem,
  catalogItemRef,
  catalogReasoningEffort,
  getLastUsedAssemblyId,
  slotHasCatalogVision,
} from "@/services/models";
import { create } from "zustand";

export function catalogComposerModel(
  catalog: ModelCatalog | undefined,
): ConversationModelOverride | null {
  const raw = (
    catalog as { composer_model?: ConversationModelOverride | null } | undefined
  )?.composer_model;
  if (!raw?.main?.model) return null;
  if (raw.main.origin !== "byok" && raw.main.origin !== "platform") return null;
  return raw;
}

export async function putComposerModel(
  override: ConversationModelOverride,
): Promise<ConversationModelOverride> {
  const snap = completeModelOverride(override);
  const saved = await api.put<ConversationModelOverride>(
    "/v1/users/me/models/composer",
    snap,
  );
  const next = completeModelOverride(saved);
  const key = pointerValue(next.main);
  queryClient.setQueryData<ModelCatalog>(modelKeys.catalog, (old) => {
    if (!old) return old;
    const knobs = { ...catalogComposerKnobs(old) };
    if (key) {
      knobs[key] = {
        reasoning_effort: next.reasoning_effort ?? null,
        context_budget: next.context_budget ?? null,
      };
    }
    return {
      ...old,
      composer_model: next,
      composer_model_knobs: knobs,
    } as ModelCatalog;
  });
  return next;
}

export type ComposerModelKnobs = {
  reasoning_effort: string | null;
  context_budget: number | null;
};

export function catalogComposerKnobs(
  catalog: ModelCatalog | undefined,
): Record<string, ComposerModelKnobs> {
  const raw = (
    catalog as
      | { composer_model_knobs?: Record<string, ComposerModelKnobs> }
      | undefined
  )?.composer_model_knobs;
  const out: Record<string, ComposerModelKnobs> = {};
  if (raw && typeof raw === "object") {
    for (const [ref, entry] of Object.entries(raw)) {
      if (!ref.trim() || !entry || typeof entry !== "object") continue;
      out[ref] = {
        reasoning_effort: entry.reasoning_effort ?? null,
        context_budget:
          typeof entry.context_budget === "number"
            ? entry.context_budget
            : null,
      };
    }
  }
  const snap = catalogComposerModel(catalog);
  const key = pointerValue(snap?.main ?? null);
  if (key && !(key in out)) {
    out[key] = {
      reasoning_effort: snap?.reasoning_effort ?? null,
      context_budget: snap?.context_budget ?? null,
    };
  }
  return out;
}

export function knobsForSlot(
  knobs: Record<string, ComposerModelKnobs> | null | undefined,
  slot: ModelProfileSlot | null | undefined,
): ComposerModelKnobs {
  const key = pointerValue(slot);
  const hit = key ? knobs?.[key] : undefined;
  return {
    reasoning_effort: hit?.reasoning_effort ?? null,
    context_budget:
      typeof hit?.context_budget === "number" ? hit.context_budget : null,
  };
}

/** Clamp stored knobs to what this leaf still lists. Unknown token / step → vendor default. */
export function clampComposerKnobs(
  slot: ModelProfileSlot | null | undefined,
  knobs: ComposerModelKnobs,
  catalogModels: ModelCatalogItem[],
): ComposerModelKnobs {
  const spec = catalogReasoningEffort(slot, catalogModels);
  const steps = contextBudgetSteps(
    catalogContextLength(catalogModels, { slot: slot ?? null }),
  );
  const effort =
    spec &&
    knobs.reasoning_effort &&
    spec.options.includes(knobs.reasoning_effort)
      ? knobs.reasoning_effort
      : null;
  const budget =
    steps &&
    knobs.context_budget != null &&
    steps.slice(0, -1).includes(knobs.context_budget)
      ? knobs.context_budget
      : null;
  return { reasoning_effort: effort, context_budget: budget };
}

export function restoreComposerKnobs(
  slot: ModelProfileSlot | null | undefined,
  knobs: Record<string, ComposerModelKnobs> | null | undefined,
  catalogModels: ModelCatalogItem[],
): ComposerModelKnobs {
  return clampComposerKnobs(slot, knobsForSlot(knobs, slot), catalogModels);
}

export type ConversationModelOverride = {
  main: ModelProfileSlot;
  worker?: ModelProfileSlot | null;
  reasoning_effort?: string | null;
  context_budget?: number | null;
};

/** Complete snapshot: every knob present so omitted keys cannot inherit assembly. */
export function completeModelOverride(
  override: ConversationModelOverride,
): ConversationModelOverride {
  return {
    main: override.main,
    worker: override.worker ?? null,
    reasoning_effort: override.reasoning_effort ?? null,
    context_budget: override.context_budget ?? null,
  };
}

export function resolveComposerOverride(args: {
  conversationId: string | null;
  conversationOverride: ConversationModelOverride | null | undefined;
  draftOverride: ConversationModelOverride | null | undefined;
  accountComposer: ConversationModelOverride | null | undefined;
}): ConversationModelOverride | null {
  if (args.conversationId) return args.conversationOverride ?? null;
  return args.draftOverride ?? args.accountComposer ?? null;
}

/**
 * New-chat picks. Assembly and model are separate: last-used is written on
 * pick; this store lets the vision hint and model chip subscribe.
 */
type ComposerProfileDraftState = {
  profileId: string | null;
  assemblyId: string | null;
  modelOverride: ConversationModelOverride | null;
  setProfileId: (profileId: string | null) => void;
  setAssemblyId: (assemblyId: string | null) => void;
  setModelOverride: (modelOverride: ConversationModelOverride | null) => void;
};

export const useComposerProfileDraftStore = create<ComposerProfileDraftState>(
  (set) => ({
    profileId: null,
    assemblyId: null,
    modelOverride: null,
    setProfileId: (profileId) => set({ profileId }),
    setAssemblyId: (assemblyId) => set({ assemblyId }),
    setModelOverride: (modelOverride) => set({ modelOverride }),
  }),
);

export function overlayProfileModel<T extends LlmModelProfileView>(
  profile: T | undefined,
  override: ConversationModelOverride | null | undefined,
): T | undefined {
  if (!profile) return undefined;
  if (!override?.main) return profile;
  const snap = completeModelOverride(override);
  return {
    ...profile,
    main: snap.main,
    worker: snap.worker ?? null,
    reasoning_effort: snap.reasoning_effort ?? null,
    context_budget: snap.context_budget ?? null,
  };
}

export function slotsFromProfile(
  profile: LlmModelProfileView | undefined,
): ConversationModelOverride | null {
  if (!profile?.main) return null;
  return {
    main: profile.main,
    worker: profile.worker ?? null,
    reasoning_effort: profile.reasoning_effort ?? null,
    context_budget: profile.context_budget ?? null,
  };
}

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

/** Session pin / new-chat draft / last-used assembly / silent account fallback. */
export function useComposerActiveProfile(): LlmModelProfileView | undefined {
  const conversationId = useChatPaneId();
  const conversations = useConversations();
  const { data: assemblyList } = useLlmModelProfiles();
  const draftAssemblyId = useComposerProfileDraftStore((s) => s.assemblyId);
  const draftOverride = useComposerProfileDraftStore((s) => s.modelOverride);
  const { data: catalog } = useModels();
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
  const assembly = lookupComposerProfile(selectedId, rows, accountDefault);
  const override = resolveComposerOverride({
    conversationId,
    conversationOverride: activeConv?.modelOverride,
    draftOverride,
    accountComposer: catalogComposerModel(catalog),
  });
  return overlayProfileModel(assembly, override);
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
  const catalog = queryClient.getQueryData<ModelCatalog>(modelKeys.catalog);
  const override = resolveComposerOverride({
    conversationId,
    conversationOverride: conv?.modelOverride,
    draftOverride: useComposerProfileDraftStore.getState().modelOverride,
    accountComposer: catalogComposerModel(catalog),
  });
  const overlaid = overlayProfileModel(profile, override);
  const budget = overlaid?.context_budget;
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

export function byokVisionDeclareRef(
  profile: VisionProfile | undefined,
): string | null {
  const main = profile?.main;
  if (!main?.model || main.origin !== "byok" || !main.provider_id) return null;
  return catalogItemRef({
    id: main.model,
    origin: "byok",
    provider_id: main.provider_id,
  });
}

export function shouldOfferComposerVisionDeclare(opts: {
  hasImage: boolean;
  profile: VisionProfile | undefined;
  catalogModels: ModelCatalogItem[];
}): boolean {
  if (!shouldShowComposerVisionHint(opts)) return false;
  return byokVisionDeclareRef(opts.profile) != null;
}

/** Pre-send muted line; does not block send. */
export const COMPOSER_VISION_HINT = "当前主模型不能看图";
export const COMPOSER_VISION_DECLARE = "记住这只能看图";
