import {
  patchConversationCache,
  useConversations,
} from "@/hooks/useConversations";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import { useComposerProfileDraftStore } from "@/lib/composerModelProfile";
import { llmModelProfileKeys } from "@/lib/queryKeys";
import { notifyError, notifySuccess } from "@/lib/toast";
import { renameAssemblyRecord } from "@/pages/toolbox/assemblyRename";
import { setConversationModelProfile } from "@/services/conversations";
import {
  type LlmModelProfileView,
  createLlmModelProfile,
  rememberAssemblySelection,
  resolveDefaultProfile,
  updateLlmModelProfile,
} from "@/services/llmModelProfiles";
import { getLastUsedAssemblyId } from "@/services/models";
import { useConversationStore } from "@/stores/conversation";
import type { Conversation } from "@/stores/conversation";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

/**
 * The assembly this shelf edits.
 * A live conversation points at its assembly. With no conversation, the page
 * is last-used (then the silent account fallback), so tool and envelope writes
 * stay on the same record the next new chat will pin.
 */
export function useEditingAssembly() {
  const queryClient = useQueryClient();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const conversations = useConversations();
  const profilesQuery = useLlmModelProfiles();
  const [pending, setPending] = useState(false);

  const conversation: Conversation | undefined = conversationId
    ? conversations.find((row) => row.id === conversationId)
    : undefined;
  const rows = profilesQuery.data?.data ?? [];
  const fallback = resolveDefaultProfile(profilesQuery.data);
  const lastUsedId = getLastUsedAssemblyId();
  const lastUsed = lastUsedId
    ? rows.find((row) => row.id === lastUsedId)
    : undefined;
  const pinned = conversation?.assemblyId
    ? rows.find((row) => row.id === conversation.assemblyId)
    : undefined;
  const profile = conversationId
    ? (pinned ?? lastUsed ?? fallback)
    : (lastUsed ?? fallback);

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: llmModelProfileKeys.list });

  const run = async (fn: () => Promise<void>, fallback: string) => {
    if (pending) return;
    setPending(true);
    try {
      await fn();
    } catch (err) {
      notifyError(err, fallback);
    } finally {
      setPending(false);
    }
  };

  const select = (id: string) =>
    run(async () => {
      if (conversationId) {
        const saved = await setConversationModelProfile(conversationId, id);
        patchConversationCache(conversationId, {
          assemblyId: saved.assemblyId ?? null,
        });
      } else {
        useComposerProfileDraftStore.getState().setAssemblyId(id);
      }
      await rememberAssemblySelection(id);
      await refresh();
    }, "能力没换成");

  const create = () =>
    run(async () => {
      const created = await createLlmModelProfile({
        name: "未命名",
        set_as_default: true,
        origin: "full",
      });
      if (conversationId) {
        const saved = await setConversationModelProfile(
          conversationId,
          created.id,
        );
        patchConversationCache(conversationId, {
          assemblyId: saved.assemblyId ?? null,
        });
      } else {
        useComposerProfileDraftStore.getState().setAssemblyId(created.id);
      }
      await rememberAssemblySelection(created.id);
      await refresh();
    }, "新建失败");

  const rename = async (name: string): Promise<boolean> => {
    if (!profile || pending) return false;
    const trimmed = name.trim();
    if (!trimmed || trimmed === profile.name) return true;
    setPending(true);
    try {
      const result = await renameAssemblyRecord({
        profileId: profile.id,
        kind: profile.kind,
        currentName: profile.name,
        nextName: trimmed,
        updateName: async (id, next) => {
          await updateLlmModelProfile(id, { name: next });
        },
      });
      if (result.changed && result.id !== profile.id && conversationId) {
        const saved = await setConversationModelProfile(
          conversationId,
          result.id,
        );
        patchConversationCache(conversationId, {
          assemblyId: saved.assemblyId ?? result.id,
        });
      }
      if (result.changed) {
        notifySuccess(`「${trimmed}」已改名`);
        await refresh();
      }
      return true;
    } catch (err) {
      notifyError(err, "名字没改成");
      return false;
    } finally {
      setPending(false);
    }
  };

  return {
    profile,
    profiles: rows,
    conversationId,
    conversation,
    pending,
    loading: profilesQuery.isLoading,
    select,
    create,
    rename,
  };
}

export type { LlmModelProfileView };
