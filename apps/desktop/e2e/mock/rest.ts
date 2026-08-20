/**
 * REST response builders constrained by `@agentcore/contract-rest-types`.
 * Keeps mock shapes typecheck-pinned to OpenAPI-generated DTOs.
 */
import type { components } from "@agentcore/contract-rest-types";

type Schemas = components["schemas"];

export type ConversationSummary = Schemas["ConversationSummary"];
export type ConversationListResponse = Schemas["ConversationListResponse"];
export type GroupedConversationsResponse =
  Schemas["GroupedConversationsResponse"];
export type LoginResponse = Schemas["LoginResponse"];
export type UserResponse = Schemas["UserResponse"];

export function emailCodeAccepted(): Schemas["EmailCodeAcceptedResponse"] {
  return { status: "accepted", expires_in: 600 };
}
export type MessageListResponse = Schemas["MessageListResponse"];
export type TurnRecoveryResponse = Schemas["TurnRecoveryResponse"];
export type StatusResponse = Schemas["StatusResponse"];
export type WorkspaceListResponse = Schemas["WorkspaceListResponse"];

const ISO = "2026-07-19T00:00:00.000Z";

export const MOCK_USER: UserResponse = {
  id: "user_e2e",
  username: "dev",
  display_name: "E2E Dev",
  email: "dev@example.com",
  email_verified_at: ISO,
  role: "user",
  created_at: ISO,
  password_must_change: false,
  avatar_url: null,
};

export function loginOk(): LoginResponse {
  return {
    mfa_required: false,
    mfa_setup_required: false,
    pending_token: null,
    user: MOCK_USER,
  };
}

export function emptyConversationList(): ConversationListResponse {
  return { data: [], page: 1, page_size: 100, total: 0 };
}

export function emptyGrouped(): GroupedConversationsResponse {
  return { folders: [], ungrouped: [] };
}

export function conversationSummary(
  partial: Partial<ConversationSummary> & { id: string },
): ConversationSummary {
  return {
    id: partial.id,
    title: partial.title ?? null,
    created_at: partial.created_at ?? ISO,
    updated_at: partial.updated_at ?? new Date().toISOString(),
    message_count: partial.message_count ?? 0,
    folder_id: partial.folder_id ?? null,
    local_container_root_id: partial.local_container_root_id ?? null,
    pinned: partial.pinned ?? false,
    archived: partial.archived ?? false,
    deep_research_auto: partial.deep_research_auto ?? false,
    context_compacted: partial.context_compacted ?? false,
    permission_axes: partial.permission_axes ?? {
      file_write: "session",
      command: "auto",
      team_kickoff: "rules",
      host: "session",
    },
    model_profile_id: partial.model_profile_id ?? null,
  };
}

export function emptyMessages(): MessageListResponse {
  return {
    data: [],
    total: 0,
    has_more_before: false,
    has_more_after: false,
    memory_updates: [],
  };
}

export function emptyRecovery(): TurnRecoveryResponse {
  return {
    live_running: false,
    paused: [],
    pending_interactions: [],
  };
}

/**
 * Seeded conversation whose GET /messages always 500s — e2e nails hydrate
 * failure shell (诚实壳层) without offline cache / route.fulfill.
 */
export const HYDRATE_FAIL_CONV_ID = "e2e0000000000000000000000hydrate";

export type { TurnRecoveryResponse };

export function statusOk(): StatusResponse {
  return { status: "ok" };
}

export function emptyWorkspaces(): WorkspaceListResponse {
  return { data: [], total: 0 };
}

/** `/readyz` is untyped in OpenAPI; keep the AuthGate contract locally. */
export function readyzOk(): { status: "ready"; database: boolean } {
  return { status: "ready", database: true };
}
