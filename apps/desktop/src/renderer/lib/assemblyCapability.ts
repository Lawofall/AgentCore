import { getConversations } from "@/hooks/useConversations";
import {
  lookupComposerProfile,
  resolveComposerProfileId,
  useComposerProfileDraftStore,
} from "@/lib/composerModelProfile";
import { queryClient } from "@/lib/queryClient";
import { llmModelProfileKeys } from "@/lib/queryKeys";
import {
  type LlmModelProfileListResponse,
  type LlmModelProfileView,
  listLlmModelProfiles,
  resolveDefaultProfile,
} from "@/services/llmModelProfiles";
import { getLastUsedAssemblyId } from "@/services/models";
import {
  type SidecarCapabilitySnapshot,
  emptySidecarCapability,
} from "@shared/sidecar-contract";

type SurfaceFields = {
  disabled_tools?: string[];
  omitted_projections?: string[];
  omitted_factory_skills?: string[];
  omit_desk_rules?: boolean;
  enabled_mcp_server_ids?: string[];
  skill_membership?: Record<string, string> | null;
};

export function capabilityFromProfile(
  profile: LlmModelProfileView,
): SidecarCapabilitySnapshot {
  const row = profile as LlmModelProfileView & SurfaceFields;
  return {
    assemblyId: profile.id,
    disabledTools: [...(row.disabled_tools ?? [])],
    omittedProjections: [...(row.omitted_projections ?? [])],
    omittedFactorySkills: [...(row.omitted_factory_skills ?? [])],
    omitDeskRules: Boolean(row.omit_desk_rules),
    enabledMcpServerIds: [...(row.enabled_mcp_server_ids ?? [])],
    skillMembership:
      row.skill_membership == null ? null : { ...row.skill_membership },
  };
}

export async function resolveSidecarCapability(
  conversationId: string,
): Promise<SidecarCapabilitySnapshot> {
  const catalog =
    queryClient.getQueryData<LlmModelProfileListResponse>(
      llmModelProfileKeys.list,
    ) ??
    (await queryClient.fetchQuery({
      queryKey: llmModelProfileKeys.list,
      queryFn: listLlmModelProfiles,
    }));
  const rows = catalog?.data ?? [];
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
    resolveDefaultProfile(catalog),
  );
  if (!profile) {
    throw new Error("未能加载这场能力");
  }
  return capabilityFromProfile(profile);
}

/** Tests: a system-like 标准 row the composer can pin. */
export function testAssemblyProfile(
  id = "asm-standard",
): LlmModelProfileView & SurfaceFields {
  return {
    id,
    name: "标准",
    kind: "system",
    is_default: true,
    recipe: "standard",
    enabled_mcp_server_ids: [],
    omitted_factory_skills: [],
    disabled_tools: ["debate", "folders", "chats"],
    omitted_projections: [],
    omit_desk_rules: false,
    skill_membership: null,
    main: { origin: "platform", provider_id: null, model: "glm-5.2" },
  };
}

export { emptySidecarCapability };
