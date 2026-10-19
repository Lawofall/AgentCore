import { conversationKeys, llmModelProfileKeys } from "@/lib/queryKeys";
import {
  listLlmModelProfiles,
  resolveDefaultProfile,
} from "@/services/llmModelProfiles";
import { setLastUsedAssemblyId } from "@/services/models";
import type { QueryClient } from "@tanstack/react-query";

/**
 * After a surface write that may have forked an official recipe, reload the
 * tray. With no conversation, last-used follows the account star so the next
 * new chat keeps the copy that was just made.
 */
export async function refreshAssembliesAfterWrite(
  queryClient: QueryClient,
  conversationId?: string | null,
): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: llmModelProfileKeys.list });
  if (conversationId) {
    await queryClient.invalidateQueries({ queryKey: conversationKeys.grouped });
    return;
  }
  const list = await queryClient.fetchQuery({
    queryKey: llmModelProfileKeys.list,
    queryFn: listLlmModelProfiles,
  });
  const star = resolveDefaultProfile(list);
  if (star) setLastUsedAssemblyId(star.id);
}
