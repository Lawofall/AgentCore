/**
 * Desktop local-store IPC (N4-A 只读离线).
 *
 * Persists opened-conversation snapshots + shell catalog under
 * `<userData>/local-store/` so a cold start without cloud can still enter the
 * shell and browse cached chats / local files. Not a second conversation
 * authority — cloud wins on reconnect. Caps: 20 opened conversations, ~50MB.
 */

/** Cached auth user (subset needed to render the shell offline). */
export interface LocalStoreUser {
  id: string;
  username: string;
  displayName: string;
  email: string | null;
  /** Absent on pre-email-verify caches; readers treat missing as unverified. */
  emailVerifiedAt?: string | null;
  role: string;
  avatarUrl: string | null;
}

/** Conversation list row kept for the offline sidebar. */
export interface LocalStoreConversationMeta {
  id: string;
  title: string;
  updatedAt: string;
  messageCount: number;
  lastMessagePreview: string | null;
  folderId?: string | null;
  localContainerRootId?: string | null;
  localRootId?: string | null;
  pinned?: boolean;
  archived?: boolean;
  /** Last time this conversation was opened (LRU eviction key). */
  openedAt: number;
  /** Serialized payload size in bytes (for the ~50MB budget). */
  byteSize: number;
}

export interface LocalStoreFolderMeta {
  id: string;
  name: string;
  mode: "local" | "cloud";
  localRootId: string | null;
  localSubpath: string | null;
}

export interface LocalStoreWorkspaceMeta {
  wsId: string;
  name: string;
  location: "cloud" | "local";
  rootId: string | null;
  subpath: string;
  hasFiles: boolean;
}

/** Per-conversation payload (messages window at last open). */
export interface LocalStoreConversationPayload {
  conversation: LocalStoreConversationMeta;
  /** JSON-serializable message rows (renderer Message shape). */
  messages: unknown[];
  memoryUpdates: unknown[];
  hasMoreBefore: boolean;
  hasMoreAfter: boolean;
}

export interface LocalStoreShellMeta {
  user: LocalStoreUser | null;
  conversations: LocalStoreConversationMeta[];
  folders: LocalStoreFolderMeta[];
  workspaces: LocalStoreWorkspaceMeta[];
  totalBytes: number;
}

export interface LocalStoreSnapshot extends LocalStoreShellMeta {
  version: 1;
}

export const LOCAL_STORE_CHANNELS = {
  hasCache: "localStore:hasCache",
  getSnapshot: "localStore:getSnapshot",
  getConversation: "localStore:getConversation",
  putOpenedConversation: "localStore:putOpenedConversation",
  putShellMeta: "localStore:putShellMeta",
  clear: "localStore:clear",
} as const;

export interface LocalStorePutShellMeta {
  user?: LocalStoreUser | null;
  folders?: LocalStoreFolderMeta[];
  workspaces?: LocalStoreWorkspaceMeta[];
  /** Replace conversation index rows without touching payloads (titles etc.). */
  conversations?: LocalStoreConversationMeta[];
}

export interface LocalStoreApi {
  hasCache(): Promise<boolean>;
  getSnapshot(): Promise<LocalStoreSnapshot | null>;
  getConversation(id: string): Promise<LocalStoreConversationPayload | null>;
  putOpenedConversation(
    payload: LocalStoreConversationPayload,
  ): Promise<LocalStoreShellMeta>;
  putShellMeta(meta: LocalStorePutShellMeta): Promise<LocalStoreShellMeta>;
  clear(): Promise<void>;
}

/** Product caps (N4-A 定案). */
export const LOCAL_STORE_MAX_CONVERSATIONS = 20;
/** Soft byte budget across all conversation payloads (~50 MiB). */
export const LOCAL_STORE_MAX_BYTES = 50 * 1024 * 1024;
