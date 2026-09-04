import { BASE_URL, api } from "@/services/api";
import { type FolderMeta, toFolder } from "@/services/folders";
import { normalizeAxes } from "@/services/permissionAxes";
import { trashBareConversationScratch } from "@/services/trashBareScratch";
import { authedFetch, saveBlob } from "@/services/workspaceHttp";
import type { Conversation } from "@/stores/conversation";
import type { components } from "@/types/api.generated";

// REST DTOs generated from the backend OpenAPI spec (root `pnpm gen:types`), aliased to
// the local names so the mappers below read unchanged (API 开发规范, 渐进迁移).
type Schemas = components["schemas"];

/** A conversation row from the list/detail endpoints (server-shaped). */
type BackendConversation = Schemas["ConversationSummary"] & {
  /** List preview; field name is fixed even if OpenAPI lags the payload. */
  last_message_preview?: string | null;
};
/** Paginated conversation list (`GET /v1/conversations`). */
type ConversationListResponse = Schemas["ConversationListResponse"];
/** Folders + ungrouped conversations in one trip (`/v1/conversations/grouped`). */
type GroupedConversationsResponse = Schemas["GroupedConversationsResponse"];

/** Placeholder shown until the backend generates a title (or for empty ones). */
const UNTITLED = "新对话";

/** Align with backend ``TITLE_MAX_CHARS`` (conversation title mint / fallback). */
export const TITLE_MAX_CHARS = 30;

/** Sidebar provisional title from the first user message (before ``title_generated``). */
export function provisionalConversationTitle(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return UNTITLED;
  return trimmed.length > TITLE_MAX_CHARS
    ? `${trimmed.slice(0, TITLE_MAX_CHARS)}…`
    : trimmed;
}

function toConversation(c: BackendConversation): Conversation {
  return {
    id: c.id,
    title: c.title?.trim() || UNTITLED,
    updatedAt: c.updated_at,
    // The list/grouped endpoints carry message_count (0 for an unsent chat).
    messageCount: c.message_count ?? 0,
    lastMessagePreview: c.last_message_preview?.trim() || null,
    folderId: c.folder_id ?? null,
    localContainerRootId: c.local_container_root_id ?? null,
    pinned: c.pinned ?? false,
    archived: c.archived ?? false,
    permissionAxes: normalizeAxes(c.permission_axes ?? undefined),
    modelProfileId: c.model_profile_id ?? null,
    contextCompacted: c.context_compacted ?? false,
    compactedThrough: c.compacted_through ?? null,
    ...(c.context_gap
      ? {
          contextGap: {
            droppedMessages: c.context_gap.dropped_messages,
            recoveryAt: c.context_gap.recovery_at ?? null,
          },
        }
      : {}),
  };
}

/** Load the user's conversations, pinned-first then most-recent (server-ordered).
 * `archived` flips to the「已归档」view (归档对话): the live list excludes archived
 * rows, this returns only them. */
export async function listConversations(
  archived = false,
): Promise<Conversation[]> {
  const res = await api.get<ConversationListResponse>(
    `/v1/conversations?page_size=100&archived=${archived}`,
  );
  return res.data.map(toConversation);
}

/**
 * Load folders + every conversation (each tagged with its `folderId`) in one
 * round trip (§七). The flat conversation list stays the store's source of
 * truth; the sidebar shows the recent few and the /conversations page derives
 * the folder groups.
 */
export async function listGrouped(): Promise<{
  folders: FolderMeta[];
  conversations: Conversation[];
}> {
  const res = await api.get<GroupedConversationsResponse>(
    "/v1/conversations/grouped",
  );
  const folders = res.folders.map((f) => {
    const row = f as typeof f & {
      my_role?: FolderMeta["myRole"];
      my_state?: FolderMeta["myState"];
      owner_user_id?: string | null;
      rel_path?: string | null;
      parent_rel_path?: string | null;
    };
    return toFolder(
      {
        id: f.id,
        name: f.name,
        mode: f.mode,
        local_root_id: f.local_root_id,
        local_subpath: f.local_subpath,
        rel_path: row.rel_path,
        parent_rel_path: row.parent_rel_path,
        my_role: row.my_role,
        my_state: row.my_state,
        owner_user_id: row.owner_user_id,
      },
      { defaultRole: "owner" },
    );
  });
  const conversations = [
    ...res.folders.flatMap((f) => f.conversations.map(toConversation)),
    ...res.ungrouped.map(toConversation),
  ];
  return { folders, conversations };
}

/**
 * Soft-delete a conversation server-side — recoverable from「最近删除」for the
 * retention window (see {@link listConversationTrash}).
 *
 * The two side effects below are *not* part of that recovery and never claim to be:
 * a 裸聊's local scratch goes to the OS recycle bin (restore it from there), and this
 * device's session roots are dropped.
 */
export async function deleteConversation(id: string): Promise<void> {
  await api.delete(`/v1/conversations/${id}`);
  // 裸聊本地 scratch → 系统回收站（软删）；项目共享目录不动。
  void trashBareConversationScratch(id);
  // W3: drop conversation session roots on this device (server grant rows cleared too).
  void window.fsApi?.clearSessionReadonlyRoots?.(id);
}

/** One recoverable conversation in「最近删除」. */
export interface DeletedConversationMeta {
  id: string;
  title: string;
  /** The project it will return to (null = 裸聊). */
  folderId: string | null;
  messageCount: number;
  deletedAt: string;
  /** Earliest moment the retention sweeper may purge it (server-computed). */
  purgeAt: string;
}

/** The conversation recycle bin plus the retention window it is governed by. */
export interface ConversationTrash {
  items: DeletedConversationMeta[];
  retentionDays: number;
}

type BackendDeletedConversation = Schemas["DeletedConversationSummary"];

function toDeletedConversation(
  c: BackendDeletedConversation,
): DeletedConversationMeta {
  return {
    id: c.id,
    title: c.title?.trim() || UNTITLED,
    folderId: c.folder_id ?? null,
    messageCount: c.message_count ?? 0,
    deletedAt: c.deleted_at,
    purgeAt: c.purge_at,
  };
}

/** 最近删除 — conversations the user deleted that are still inside the retention window. */
export async function listConversationTrash(): Promise<ConversationTrash> {
  const res = await api.get<Schemas["DeletedConversationListResponse"]>(
    "/v1/conversations/trash",
  );
  return {
    items: res.data.map(toDeletedConversation),
    retentionDays: res.retention_days,
  };
}

/**
 * Restore a deleted conversation. Past the retention window the server answers 409
 * (「该对话已被清理」) — a real window, not something to retry around.
 *
 * The returned row is the live conversation, back in the project / pin / archive
 * state it was deleted in.
 */
export async function restoreConversation(id: string): Promise<Conversation> {
  const res = await api.post<BackendConversation>(
    `/v1/conversations/trash/${id}/restore`,
  );
  return toConversation(res);
}

/** Persist a new conversation title. */
export async function renameConversation(
  id: string,
  title: string,
): Promise<void> {
  await api.patch(`/v1/conversations/${id}`, { title });
}

/**
 * Local-first parallel title mint (``POST …/auto-title``).
 * Awaits the shared server mint core (user message only; no assistant reply).
 * Returns the resulting title, or ``null`` on failure (caller keeps provisional).
 */
export async function requestAutoTitle(
  conversationId: string,
  userMessage: string,
): Promise<string | null> {
  const trimmed = userMessage.trim();
  if (!trimmed) return null;
  try {
    const res = await api.post<{ title: string }>(
      `/v1/conversations/${conversationId}/auto-title`,
      { user_message: trimmed },
    );
    const title = res.title?.trim();
    return title || null;
  } catch {
    return null;
  }
}

/** Clone a conversation into a brand-new one carrying a copy of its transcript
 * (克隆对话). Returns the new (server-shaped) row — same folder as the source,
 * titled「… 副本」— so the caller can insert it into the sidebar and open it. */
export async function duplicateConversation(id: string): Promise<Conversation> {
  const res = await api.post<BackendConversation>(
    `/v1/conversations/${id}/duplicate`,
  );
  return toConversation(res);
}

/** Export formats offered by the backend (导出对话): a clean Markdown record or a
 * full-fidelity JSON dump. */
export type ExportFormat = "md" | "json";

/** Pull the download filename from a Content-Disposition header, preferring the
 * RFC 5987 `filename*=UTF-8''…` form (carries the non-ASCII title) over the ASCII
 * `filename="…"` fallback; returns `fallback` when the header is absent/unreadable
 * (the server must expose the header via CORS for this to be populated). */
function filenameFromDisposition(res: Response, fallback: string): string {
  const cd = res.headers.get("Content-Disposition") ?? "";
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1]);
    } catch {
      // Malformed percent-encoding — fall through to the ASCII form.
    }
  }
  const ascii = /filename="?([^";]+)"?/i.exec(cd);
  return ascii?.[1]?.trim() || fallback;
}

/** Download a conversation's full transcript as a file (导出对话). Streams the
 * attachment via the cookie-authed raw-bytes path (bypassing the JSON `api`
 * helper) and saves it with the server's sanitized filename. */
export async function exportConversation(
  id: string,
  format: ExportFormat = "md",
): Promise<void> {
  const res = await authedFetch(
    `${BASE_URL}/v1/conversations/${id}/export?format=${format}`,
  );
  const blob = await res.blob();
  await saveBlob(blob, filenameFromDisposition(res, `conversation.${format}`));
}

/** Pin / unpin a conversation (置顶对话). Returns the updated row. */
export async function setConversationPinned(
  id: string,
  pinned: boolean,
): Promise<Conversation> {
  const res = await api.patch<BackendConversation>(`/v1/conversations/${id}`, {
    pinned,
  });
  return toConversation(res);
}

/** Archive / unarchive a conversation (归档对话, reversible). Returns the updated
 * row — unarchive (archived=false) yields a live-list row to put back. */
export async function setConversationArchived(
  id: string,
  archived: boolean,
): Promise<Conversation> {
  const res = await api.patch<BackendConversation>(`/v1/conversations/${id}`, {
    archived,
  });
  return toConversation(res);
}

/**
 * 切换会话使用的模型组合。传 profile id 固定本会话组合（活引用定义）；
 * 传 `null` = 再钉当时账号默认（不是活跟随）。不可用 / 越权时后端返 422（由调用方 toast 呈现）。
 */
export async function setConversationModelProfile(
  id: string,
  profileId: string | null,
): Promise<Conversation> {
  const model_profile_id = profileId?.trim() ? profileId.trim() : null;
  const res = await api.patch<BackendConversation>(`/v1/conversations/${id}`, {
    model_profile_id,
  });
  return toConversation(res);
}
