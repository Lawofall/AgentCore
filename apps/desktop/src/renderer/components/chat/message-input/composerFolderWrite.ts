import { getConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import { type FolderMeta, canWriteFolder } from "@/services/folders";
import type { Conversation } from "@/stores/conversation";
import { type DraftWorkspaceIntent, useFoldersStore } from "@/stores/folders";

/** Button title / handleSend toast — 与离线硬禁并列，短中文。 */
export const COMPOSER_FOLDER_READ_ONLY_HINT = "只读协作桌，无法发送";

type ConversationFolderRef = Pick<Conversation, "id" | "folderId">;

/**
 * Current conversation → `conversations.folderId`；draft → `draftWorkspaceIntent`.
 * Missing folder / 裸聊 → `undefined`（不拦，后端仍是底线）。
 * `folders` must be the accessible union（useFolders / getFolders，含与我共享）.
 */
export function resolveComposerFolder({
  conversationId,
  conversations,
  folders,
  draftIntent,
}: {
  conversationId: string | null;
  conversations: readonly ConversationFolderRef[];
  folders: readonly FolderMeta[];
  draftIntent: DraftWorkspaceIntent;
}): FolderMeta | undefined {
  let folderId: string | null | undefined;
  if (conversationId) {
    folderId =
      conversations.find((c) => c.id === conversationId)?.folderId ?? null;
  } else if (draftIntent.kind === "folder") {
    folderId = draftIntent.folderId;
  } else {
    folderId = null;
  }
  if (!folderId) return undefined;
  return folders.find((f) => f.id === folderId);
}

/** Viewer on a known desk → block. 找不到 folder 或裸聊 → 不拦。 */
export function isComposerFolderWriteBlocked(args: {
  conversationId: string | null;
  conversations: readonly ConversationFolderRef[];
  folders: readonly FolderMeta[];
  draftIntent: DraftWorkspaceIntent;
}): boolean {
  const folder = resolveComposerFolder(args);
  return folder != null && !canWriteFolder(folder);
}

/** Imperative snapshot for handleSend（按钮已 disabled；此处兜底防键盘）. */
export function isCurrentComposerFolderWriteBlocked(
  conversationId: string | null,
): boolean {
  return isComposerFolderWriteBlocked({
    conversationId,
    conversations: getConversations(),
    folders: getFolders(),
    draftIntent: useFoldersStore.getState().draftWorkspaceIntent,
  });
}
