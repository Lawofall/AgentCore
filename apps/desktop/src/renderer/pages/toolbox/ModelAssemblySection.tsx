import { patchConversationCache } from "@/hooks/useConversations";
import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import { llmModelProfileKeys } from "@/lib/queryKeys";
import { type ProfileDraft, ProfileEditor } from "@/pages/more/ModelSettings";
import { useEditingAssembly } from "@/pages/toolbox/useEditingAssembly";
import { setConversationModelProfile } from "@/services/conversations";
import {
  profileSlotSummary,
  setDefaultLlmModelProfile,
  updateLlmModelProfile,
} from "@/services/llmModelProfiles";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

/**
 * Model columns of the assembly this shelf is editing.
 * A change writes immediately. A preset materializes, then the model
 * is written onto that row.
 */
export function ModelAssemblySection() {
  const queryClient = useQueryClient();
  const providersQuery = useLlmProviders();
  const { data: catalog } = useModels();
  const { profile, loading, conversationId } = useEditingAssembly();
  const [warnings, setWarnings] = useState<string[]>([]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: llmModelProfileKeys.list });
  };

  if (loading && !profile) {
    return <p className="text-sm text-muted-foreground">加载中…</p>;
  }
  if (!profile) {
    return <p className="text-sm text-muted-foreground">还没有装配。</p>;
  }

  const providers = providersQuery.data?.providers ?? [];
  const platformAvailable = providersQuery.data?.platform_available ?? false;
  const catalogModels = catalog?.models ?? [];
  const editable = profile.kind === "user" || profile.kind === "system";

  const onSave = async (draft: ProfileDraft) => {
    if (!draft.main) throw new Error("主模型必填");
    let id = profile.id;
    if (profile.kind === "system") {
      const materialized = await setDefaultLlmModelProfile(profile.id);
      id = materialized.id;
      if (conversationId) {
        const saved = await setConversationModelProfile(conversationId, id);
        patchConversationCache(conversationId, {
          assemblyId: saved.assemblyId ?? id,
        });
      }
    }
    const updated = await updateLlmModelProfile(id, {
      main: draft.main,
      worker: draft.worker,
      background: draft.background,
      vision: draft.vision,
      reasoning_effort: draft.reasoning_effort,
      context_budget: draft.context_budget,
    });
    const next = (updated.warnings ?? [])
      .map((item) => item.trim())
      .filter(Boolean);
    setWarnings(next);
    refresh();
  };

  return (
    <div>
      {editable ? (
        <ProfileEditor
          key={profile.id}
          title=""
          showTitle={false}
          showCancel={false}
          framed={false}
          surface="shelf"
          providers={providers}
          catalog={catalog}
          catalogModels={catalogModels}
          platformAvailable={platformAvailable}
          initial={{
            name: profile.name,
            main: profile.main ?? null,
            worker: profile.worker ?? null,
            background: profile.background ?? null,
            vision: profile.vision ?? null,
            reasoning_effort: profile.reasoning_effort ?? null,
            context_budget: profile.context_budget ?? null,
          }}
          saveWarnings={warnings}
          pending={false}
          onCancel={() => undefined}
          onSave={onSave}
        />
      ) : (
        <p className="text-sm text-foreground">
          {profile.main
            ? profileSlotSummary(profile, catalogModels)
            : profile.name}
        </p>
      )}
      {providers.length === 0 && !platformAvailable ? (
        <p className="mt-3 text-xs text-muted-foreground">
          还没有可用模型。请{" "}
          <Link
            to="/more/providers"
            className="text-primary underline-offset-2 hover:underline"
          >
            接入服务商
          </Link>
          。
        </p>
      ) : null}
    </div>
  );
}
