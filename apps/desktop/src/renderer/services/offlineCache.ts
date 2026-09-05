/**
 * N4-A 只读离线 — renderer helpers over `window.localStoreApi`.
 *
 * Desktop-only; web / preview / tests without the IPC no-op gracefully.
 */
import {
  type GroupedConversations,
  getConversations,
} from "@/hooks/useConversations";
import { previewFromOpenedWindow } from "@/lib/conversationListPreview";
import { queryClient } from "@/lib/queryClient";
import { conversationKeys, workspaceKeys } from "@/lib/queryKeys";
import type { FolderMeta } from "@/services/folders";
import type { WorkspaceInfo } from "@/services/workspaces";
import type { AuthUser } from "@/stores/auth";
import {
  type Conversation,
  type MemoryUpdate,
  type Message,
  getRuntime,
  isMessageWindowStrictlyRicher,
} from "@/stores/conversation";
import type {
  LocalStoreConversationMeta,
  LocalStoreConversationPayload,
  LocalStoreSnapshot,
  LocalStoreUser,
} from "@shared/local-store-contract";

function api() {
  return typeof window !== "undefined" ? window.localStoreApi : undefined;
}

function toStoreUser(user: AuthUser): LocalStoreUser {
  return {
    id: user.id,
    username: user.username,
    displayName: user.displayName,
    email: user.email,
    emailVerifiedAt: user.emailVerifiedAt,
    role: user.role,
    avatarUrl: user.avatarUrl,
  };
}

export function fromStoreUser(user: LocalStoreUser): AuthUser {
  return {
    id: user.id,
    username: user.username,
    displayName: user.displayName,
    email: user.email,
    emailVerifiedAt: user.emailVerifiedAt ?? null,
    role: user.role,
    avatarUrl: user.avatarUrl,
  };
}

function toStoreConvMeta(
  c: Conversation,
  openedAt: number,
  byteSize: number,
): LocalStoreConversationMeta {
  return {
    id: c.id,
    title: c.title,
    updatedAt: c.updatedAt,
    messageCount: c.messageCount,
    lastMessagePreview: c.lastMessagePreview,
    folderId: c.folderId,
    localContainerRootId: c.localContainerRootId,
    localRootId: c.localRootId,
    pinned: c.pinned,
    archived: c.archived,
    openedAt,
    byteSize,
  };
}

export function fromStoreConvMeta(c: LocalStoreConversationMeta): Conversation {
  return {
    id: c.id,
    title: c.title,
    updatedAt: c.updatedAt,
    messageCount: c.messageCount,
    lastMessagePreview: c.lastMessagePreview,
    folderId: c.folderId,
    localContainerRootId: c.localContainerRootId,
    localRootId: c.localRootId,
    pinned: c.pinned,
    archived: c.archived,
  };
}

/** True when local-store has a prior user and/or opened conversations. */
export async function hasOfflineCache(): Promise<boolean> {
  const a = api();
  if (!a) return false;
  try {
    return await a.hasCache();
  } catch {
    return false;
  }
}

export async function loadOfflineSnapshot(): Promise<LocalStoreSnapshot | null> {
  const a = api();
  if (!a) return null;
  try {
    return await a.getSnapshot();
  } catch {
    return null;
  }
}

/** Persist last-known user + folder/workspace catalog (online refresh). */
export async function cacheShellMeta(input: {
  user?: AuthUser | null;
  folders?: FolderMeta[];
  workspaces?: WorkspaceInfo[];
  conversations?: Conversation[];
}): Promise<void> {
  const a = api();
  if (!a) return;
  try {
    await a.putShellMeta({
      user:
        input.user === undefined
          ? undefined
          : input.user
            ? toStoreUser(input.user)
            : null,
      folders: input.folders,
      workspaces: input.workspaces,
      conversations: input.conversations?.map((c) =>
        toStoreConvMeta(c, Date.now(), 0),
      ),
    });
  } catch (err) {
    console.warn("[local-store] putShellMeta failed", err);
  }
}

/** Cache an opened conversation's latest message window (N4-A: only opened). */
export async function cacheOpenedConversation(input: {
  conversation: Conversation;
  messages: Message[];
  memoryUpdates: MemoryUpdate[];
  hasMoreBefore: boolean;
  hasMoreAfter: boolean;
}): Promise<void> {
  const a = api();
  if (!a) return;
  const messages = input.messages.map((m) => ({
    ...m,
    isStreaming: false,
    composingTool: null,
  }));
  const payload: LocalStoreConversationPayload = {
    conversation: toStoreConvMeta(input.conversation, Date.now(), 0),
    messages,
    memoryUpdates: input.memoryUpdates,
    hasMoreBefore: input.hasMoreBefore,
    hasMoreAfter: input.hasMoreAfter,
  };
  // byteSize filled by main after serialize; meta.conversation.byteSize updated there.
  try {
    await a.putOpenedConversation(payload);
  } catch (err) {
    console.warn("[local-store] putOpenedConversation failed", err);
  }
}

/**
 * Persist a trusted latest window into the offline opened cache.
 * Call only after a gate-passed write (loadLatestWindow / cold reconcile /
 * live-tail snapshot). A thinner incoming window must not replace a thicker
 * opened snapshot — that is the refresh「最后一轮一会儿有一会儿没有」.
 */
export async function persistOpenedCache(
  id: string,
  messages: Message[],
  memoryUpdates: MemoryUpdate[],
  flags: { hasMoreBefore: boolean; hasMoreAfter: boolean },
): Promise<void> {
  // Empty GET / reconcile must not poison the opened snapshot.
  if (messages.length === 0) return;
  const cached = await loadCachedConversation(id);
  const cachedMessages = (cached?.messages ?? []) as Message[];
  if (
    cachedMessages.length > 0 &&
    !isMessageWindowStrictlyRicher(messages, cachedMessages)
  ) {
    return;
  }
  const listed = getConversations().find((c) => c.id === id);
  const lastMessagePreview = previewFromOpenedWindow(
    messages,
    listed?.lastMessagePreview,
  );
  const conversation = listed
    ? {
        ...listed,
        messageCount: messages.length,
        lastMessagePreview,
      }
    : {
        id,
        title: "对话",
        updatedAt: new Date().toISOString(),
        messageCount: messages.length,
        lastMessagePreview,
      };
  await cacheOpenedConversation({
    conversation,
    messages,
    memoryUpdates,
    hasMoreBefore: flags.hasMoreBefore,
    hasMoreAfter: flags.hasMoreAfter,
  });
}

/** Persist the resident in-memory slice when it is strictly richer than cache. */
export function persistResidentOpenedCache(conversationId: string): void {
  const rt = getRuntime(conversationId);
  if (rt.messages.length === 0) return;
  void persistOpenedCache(conversationId, rt.messages, rt.memoryUpdates, {
    hasMoreBefore: rt.hasMoreBefore,
    hasMoreAfter: rt.hasMoreAfter,
  });
}

export async function loadCachedConversation(
  id: string,
): Promise<LocalStoreConversationPayload | null> {
  const a = api();
  if (!a) return null;
  try {
    return await a.getConversation(id);
  } catch {
    return null;
  }
}

export async function clearOfflineCache(): Promise<void> {
  const a = api();
  if (!a) return;
  try {
    await a.clear();
  } catch (err) {
    console.warn("[local-store] clear failed", err);
  }
}

/**
 * Seed React Query + return cached user so AuthGate can enter the shell offline.
 * Returns null when there is nothing to bootstrap from.
 */
export async function hydrateOfflineShell(): Promise<AuthUser | null> {
  const snap = await loadOfflineSnapshot();
  if (!snap?.user) return null;

  const conversations = snap.conversations
    .slice()
    .sort((a, b) => b.openedAt - a.openedAt)
    .map(fromStoreConvMeta);

  const grouped: GroupedConversations = {
    folders: snap.folders.map((f) => ({
      id: f.id,
      name: f.name,
      mode: f.mode,
      localRootId: f.localRootId,
      localSubpath: f.localSubpath,
    })),
    conversations,
  };
  queryClient.setQueryData(conversationKeys.grouped, grouped);
  queryClient.setQueryData(
    workspaceKeys.list,
    snap.workspaces.map(
      (w): WorkspaceInfo => ({
        wsId: w.wsId,
        name: w.name,
        location: w.location,
        rootId: w.rootId,
        subpath: w.subpath,
        hasFiles: w.hasFiles,
      }),
    ),
  );

  return fromStoreUser(snap.user);
}
