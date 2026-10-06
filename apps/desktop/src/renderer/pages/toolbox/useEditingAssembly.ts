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
  resolveDefaultProfile,
  setDefaultLlmModelProfile,
  updateLlmModelProfile,
} from "@/services/llmModelProfiles";
import { setLastUsedAssemblyId } from "@/services/models";
import { useConversationStore } from "@/stores/conversation";
import type { Conversation } from "@/stores/conversation";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

/**
 * The assembly this shelf edits.
 * A live conversation points at its assembly. With no conversation, the page
 * is the starred one — picking another stars it, so tool and envelope writes
 * stay on the same record 总 names.
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
  const starred = resolveDefaultProfile(profilesQuery.data);
  const pinned = conversation?.assemblyId
    ? rows.find((row) => row.id === conversation.assemblyId)
    : undefined;
  const profile = conversationId ? (pinned ?? starred) : starred;

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
        await refresh();
        return;
      }
      await setDefaultLlmModelProfile(id);
      setLastUsedAssemblyId(id);
      useComposerProfileDraftStore.getState().setAssemblyId(id);
      await refresh();
    }, "装配没换成");

  const star = (id?: string) =>
    run(async () => {
      const target = id ?? profile?.id;
      if (!target) return;
      await setDefaultLlmModelProfile(target);
      if (!conversationId) {
        setLastUsedAssemblyId(target);
        useComposerProfileDraftStore.getState().setAssemblyId(target);
      }
      await refresh();
    }, "星标没设上");

  const createFromCurrent = () =>
    run(async () => {
      const created = await createLlmModelProfile({
        name: "未命名",
        set_as_default: !conversationId,
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
        setLastUsedAssemblyId(created.id);
        useComposerProfileDraftStore.getState().setAssemblyId(created.id);
      }
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
        materialize: async (id) => {
          const row = await setDefaultLlmModelProfile(id);
          return row.id;
        },
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
    star,
    createFromCurrent,
    rename,
  };
}

export type { LlmModelProfileView };
