/**
 * Central registry of React Query keys. Keeping every key here (rather than
 * inline string arrays at each call site) lets a mutation invalidate exactly the
 * queries it affects without guessing the shape, and makes the cached REST
 * surface discoverable in one place as more resources migrate onto React Query.
 */
export const conversationKeys = {
  all: ["conversations"] as const,
  /** Folders + conversations in one trip (`GET /v1/conversations/grouped`). */
  grouped: ["conversations", "grouped"] as const,
  /** Archived conversations (`GET /v1/conversations?archived=true`) — the
   * on-demand「已归档」view, separate from the live grouped cache. */
  archived: ["conversations", "archived"] as const,
  /** 最近删除（`GET /v1/conversations/trash`）— 已删对话 + 保留天数。Shares the
   * 「最近删除」view with {@link folderKeys.trash}; two trips, one pane. */
  trash: ["conversations", "trash"] as const,
  /** 项目协作时间线（`GET /v1/folders/{id}/collaboration-timeline`）。 */
  collaborationTimeline: (folderId: string) =>
    ["collaboration-timeline", folderId] as const,
};

/** 项目（folder）自有的查询面。The live folder list itself rides on
 * `conversationKeys.grouped`; recycle bin + collaboration-desk lists are extra
 * trips. */
export const folderKeys = {
  all: ["folders"] as const,
  /** 最近删除（`GET /v1/folders/trash`）— 已删项目 + 保留天数. */
  trash: ["folders", "trash"] as const,
  /** Member desks (`GET /v1/folders/shared-with-me`). */
  sharedWithMe: ["folders", "shared-with-me"] as const,
  pendingInvites: ["folders", "pending-invites"] as const,
  members: (folderId: string) => ["folders", "members", folderId] as const,
};

export const workspaceKeys = {
  all: ["workspaces"] as const,
  /** The user's workspaces (= folders, cloud + local) for the 文件 hub rail
   * (`GET /v1/workspaces`). */
  list: ["workspaces", "list"] as const,
};

/** Conversation-scoped external directory grants (`external/<alias>/`). */
export const externalGrantKeys = {
  all: ["external-grants"] as const,
  list: (conversationId: string) =>
    ["external-grants", "list", conversationId] as const,
};

/** 设置·模型配置的服务商列表（`GET /v1/users/me/llm-providers`）。 */
export const llmProviderKeys = {
  all: ["llm-providers"] as const,
  /** The user's BYOK provider list + deployment caps. */
  list: ["llm-providers", "list"] as const,
};

/** 设置·Git 凭据（`GET /v1/users/me/git-credentials` · G3）。 */
export const gitCredentialKeys = {
  all: ["git-credentials"] as const,
  detail: ["git-credentials", "detail"] as const,
};

/** 账号模型组合（`GET /v1/users/me/llm-model-profiles`）。 */
export const llmModelProfileKeys = {
  all: ["llm-model-profiles"] as const,
  list: ["llm-model-profiles", "list"] as const,
};

/** 槽位编辑用的模型目录（`GET /v1/users/me/models`）。 */
export const modelKeys = {
  all: ["models"] as const,
  /** The user's selectable model catalog + current account model. */
  catalog: ["models", "catalog"] as const,
};
