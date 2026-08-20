import { apiFetch } from "@/api/client";
// Conversation REST for the mobile client (前端技术与架构 §七 · 会话管理).
//
// Bearer-authenticated reads/writes over the same cloud endpoints the desktop uses
// (api/routes/conversations.py). Pure data fetch — the chat transport (SSE) lives in
// stream.ts. REST DTOs track the backend OpenAPI spec via @agentcore/contract-rest-types;
// `runs.events` stays typed as SSEEvent[] (opaque JSON in OpenAPI — API 开发规范).
import type { components } from "@/types/api.generated";
import type {
  Citation,
  ContextBlockWire,
  ProcessStep,
  SSEEvent,
  TurnEvidenceLedgerEntry,
  UsageBreakdown,
} from "@agentcore/contract-types";

type Schemas = components["schemas"];

/** A conversation row from the list/detail endpoints (server-shaped). */
export type ConversationSummary = Schemas["ConversationSummary"];

/** Terminal error on a failed turn (schemas.py RunError) — cold-path error card. */
export type RunError = Schemas["RunError"];

/** An assistant message's persisted replay payload (schemas.py RunsPayload).
 *  `events` is a MULTI-agent turn's ordered run/tool SSE journal (empty `[]` for a
 *  single-agent turn) — re-fold it through the SAME {@link fold} as the live stream to
 *  reproduce the team graph on reload. `process` is a SINGLE-agent turn's 思考+工具
 *  timeline (verbatim ProcessStep[]; null unless a tool ran). `null` whole payload for a
 *  plain text turn. */
export interface RunsPayload {
  events: SSEEvent[];
  finish_reason: string | null;
  process: ProcessStep[] | null;
  /** 收到的上下文 · CEO 侧 (上下文传递可视化 通道①): the captain's `run_context` blocks,
   *  persisted turn-level so a pure-chat turn (empty `events`) still replays the CEO's
   *  received context on reload. `null` unless the captain shipped context. */
  captain_context?: ContextBlockWire[] | null;
  /** 预检警告（P2 DURABLE）：lifted turn_warning for plain-chat reload. */
  turn_warning?: string | null;
  /** 报错回合 terminal error（冷加载 inline 错因；null = 干净回合）. */
  error?: RunError | null;
  /** 裸聊写盘自动建文件夹告知（§5.4）；冷加载走 lifted 字段，不依赖 journal 里是否还留着事件. */
  auto_folder?: Schemas["AutoFolderNotice"] | null;
}

/** A user message's attachment as persisted (composer 附件). The agent-chat send ships the
 *  file text inline; the server durably stores it, so a reloaded turn carries only display
 *  metadata (name + truncation), not the content — enough to show context chips. */
export type AttachmentMeta = Pick<
  Schemas["StoredAttachment"],
  "name" | "truncated"
>;

export interface MessageDetail {
  id: string;
  role: string;
  content: string | null;
  reasoning_content: string | null;
  citations: Citation[];
  /** 回合调研台账（引用即出处 P1, DERIVED）：messages.evidence_ledger；`#rN` 冷启动。 */
  evidenceLedger?: TurnEvidenceLedgerEntry[];
  runs: RunsPayload | null;
  attachments?: AttachmentMeta[];
  /** Conversation-page ``@`` role chips (REST ``agent_mentions``; 旁路 attachments). */
  agentMentions?: { agentId: string; role: string }[];
  /** Progressive assistant-row lifecycle (``usage.status`` · P4 hydrate). */
  status?: "running" | "complete" | "incomplete" | "failed" | null;
  /** Cold-path pause latch (``usage.paused``): hydrate as paused, not streaming. */
  paused?: boolean | null;
  /** Turn result quality (``usage.outcome``). ``paused`` = CEO continue face. */
  outcome?: "ok" | "partial" | "paused" | "error" | null;
  /** 回合 ¥ 成本 (P2 DERIVED)：messages.cost 列；重载 footer 直接用。 */
  cost?: Schemas["CostBreakdown"] | null;
  /** 消息来源（如 execution_harvest 系统收口）；正文前缀为旧数据兜底. */
  origin?: string | null;
  /** 曾中断恢复（``usage.recovered``）：崩溃重驱把本回合原地跑完，标记如实保留. */
  recovered?: boolean | null;
  /** 回合日志关联 id（messages.trace_id）—「复制排查包」冷启动. */
  trace_id?: string | null;
  /** 回合墙钟用时 (ms)：与 message_end.duration_ms 同锚；重载自 usage JSON. */
  duration_ms?: number | null;
  /** Token 用量（messages.usage）；Footer 明细. */
  usage?: UsageBreakdown | null;
  /** ReAct 轮次（messages.rounds）. */
  rounds?: number | null;
  /** 回合协作计数（`usage.collab`）：内部口径，用户面不展示。live 走 message_end；
   *  这里是重载路径——message_end 是 DERIVED（不进 journal），历史只能从这列拿。 */
  collab?: Schemas["TurnCollabMetrics"] | null;
  /** 本回合团队状态（journal 派生；没派工是 no_batch）。live 走 message_end。 */
  team_batch?: import("@agentcore/contract-types").TeamBatchStatus | null;
  created_at: string;
}

/** The latest page of the user's conversations (newest-first). `archived` selects the
 *  「已归档」view; the default live list excludes archived rows (backend default). */
export async function listConversations(
  archived = false,
): Promise<ConversationSummary[]> {
  const res = await apiFetch(
    `/v1/conversations?page=1&page_size=50&archived=${archived}`,
  );
  if (!res.ok) throw new Error(`加载会话列表失败 (${res.status})`);
  const data = (await res.json()) as Schemas["ConversationListResponse"];
  return data.data;
}

/** Fetch one conversation (owner-scoped). Includes ``permission_axes``. */
export async function getConversation(
  id: string,
): Promise<ConversationSummary> {
  const res = await apiFetch(`/v1/conversations/${id}`);
  if (!res.ok) throw new Error(`加载会话失败 (${res.status})`);
  return (await res.json()) as ConversationSummary;
}

/** Rename a conversation (对话管理 · 重命名). Returns the updated summary. */
export async function renameConversation(
  id: string,
  title: string,
): Promise<ConversationSummary> {
  const res = await apiFetch(`/v1/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`重命名失败 (${res.status})`);
  return (await res.json()) as ConversationSummary;
}

/** Pin / unpin a conversation (置顶对话). Returns the updated summary. */
export async function setConversationPinned(
  id: string,
  pinned: boolean,
): Promise<ConversationSummary> {
  const res = await apiFetch(`/v1/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pinned }),
  });
  if (!res.ok) {
    throw new Error(`${pinned ? "置顶" : "取消置顶"}失败 (${res.status})`);
  }
  return (await res.json()) as ConversationSummary;
}

/** Switch this conversation's model combination (定案 B · 拍快照).
 *  Pass a concrete profile id to re-snapshot, or null to re-pin the then-current
 *  account default (not live follow). Returns the updated summary
 *  (`model_profile_id` is authoritative). */
export async function setConversationModelProfile(
  id: string,
  profileId: string | null,
): Promise<ConversationSummary> {
  const res = await apiFetch(`/v1/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_profile_id: profileId }),
  });
  if (!res.ok) {
    let message = `切换模型组合失败 (${res.status})`;
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      if (body.error?.message) message = body.error.message;
    } catch {
      /* non-JSON body — keep the status-only phrasing */
    }
    throw new Error(message);
  }
  return (await res.json()) as ConversationSummary;
}

/** Archive (hide from the live list) or restore a conversation — reversible, no data
 *  loss (对话管理 · 归档/恢复). */
export async function setConversationArchived(
  id: string,
  archived: boolean,
): Promise<void> {
  const res = await apiFetch(`/v1/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ archived }),
  });
  if (!res.ok) {
    throw new Error(`${archived ? "归档" : "恢复"}失败 (${res.status})`);
  }
}

/** A recoverable row in「最近删除」(server-shaped). */
export type DeletedConversationSummary = Schemas["DeletedConversationSummary"];

/** OpenAPI body for `GET /v1/conversations/trash`. */
export type DeletedConversationListResponse =
  Schemas["DeletedConversationListResponse"];

/** Client view of the conversation recycle bin. */
export interface ConversationTrash {
  items: DeletedConversationSummary[];
  retention_days: number;
  total: number;
}

/** Delete a conversation (对话管理 · 删除).
 *
 *  Server-side this is a **soft** delete: 可从「最近删除」保留期内恢复
 *  （见 {@link listConversationTrash}）。不要对用户说「永久删除 / 无法恢复」。 */
export async function deleteConversation(id: string): Promise<void> {
  const res = await apiFetch(`/v1/conversations/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`删除失败 (${res.status})`);
}

/** 最近删除 — conversations still inside the retention window. */
export async function listConversationTrash(): Promise<ConversationTrash> {
  const res = await apiFetch("/v1/conversations/trash");
  if (!res.ok) throw new Error(`加载最近删除失败 (${res.status})`);
  const data = (await res.json()) as DeletedConversationListResponse;
  return {
    items: data.data,
    retention_days: data.retention_days,
    total: data.total,
  };
}

/**
 * Restore a soft-deleted conversation. Past the retention window (or losing the
 * purge race) the server answers 409 with a real reason — surface that, never
 * pretend the row came back.
 */
export async function restoreConversation(
  id: string,
): Promise<ConversationSummary> {
  const res = await apiFetch(`/v1/conversations/trash/${id}/restore`, {
    method: "POST",
  });
  if (!res.ok) {
    let message = `恢复失败 (${res.status})`;
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      if (body.error?.message) message = body.error.message;
    } catch {
      /* non-JSON body — keep the status-only phrasing */
    }
    throw new Error(message);
  }
  return (await res.json()) as ConversationSummary;
}

/** Create a fresh cloud conversation and return its id (skeleton: no folder/mode).
 *  Optional ``folder_id`` files the chat into an existing cloud folder at birth
 *  (归属中途不改挂). Optional ``permission_axes`` seeds this session (else account
 *  default recipe). Optional ``model_profile_id`` snapshots that combination at
 *  create (定案 B); omit to let the server write the then-current account default.
 *  Optional ``client_request_id`` is the caller's idempotency key — the server answers a
 *  repeat of the same key with the conversation it already created, so a double-fire
 *  (双击 / 重试) can't leave a duplicate 会话 behind. */
export async function createConversation(
  title?: string,
  opts?: {
    folder_id?: string | null;
    permission_axes?: Schemas["PermissionAxesModel"] | null;
    model_profile_id?: string | null;
    client_request_id?: string | null;
  },
): Promise<string> {
  const res = await apiFetch("/v1/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: title ?? null,
      ...(opts?.folder_id ? { folder_id: opts.folder_id } : {}),
      ...(opts?.permission_axes
        ? { permission_axes: opts.permission_axes }
        : {}),
      ...(opts?.model_profile_id
        ? { model_profile_id: opts.model_profile_id }
        : {}),
      ...(opts?.client_request_id
        ? { client_request_id: opts.client_request_id }
        : {}),
    }),
  });
  if (!res.ok) throw new Error(`创建会话失败 (${res.status})`);
  const data = (await res.json()) as { id: string };
  return data.id;
}

/** Sidebar-shaped grouping: folders (incl. empty) + ungrouped 裸聊. Live list only
 *  (archived chats stay on {@link listConversations} with `archived=true`). */
export type FolderGroup = Schemas["FolderGroup"];
export type GroupedConversations = Schemas["GroupedConversationsResponse"];

export async function listConversationsGrouped(): Promise<GroupedConversations> {
  const res = await apiFetch("/v1/conversations/grouped");
  if (!res.ok) throw new Error(`加载会话列表失败 (${res.status})`);
  return (await res.json()) as GroupedConversations;
}

/** One applied memory change in a 记忆已更新 card (Agent记忆与知识系统 §1.6). */
export type MemoryUpdateItem = Schemas["MemoryUpdateItemView"];

type MemoryUpdateView = Schemas["MemoryUpdateView"];

/**
 * One offline-consolidation pass — what the AI remembered FROM this conversation (写也可见,
 *  §1.6). CamelCase client projection of OpenAPI `MemoryUpdateView` (M17 exemption:
 *  OpenAPI has no camelCase conversation-tail schema). Returned ONLY with the latest
 *  messages window. Mobile has no per-user firehose; ChatPage polls after message_end.
 *
 *  `kind` is the generated wire type (not a narrower local union). Pass it through
 *  unchanged so a new value cannot be silently rewritten to `"semantic"`.
 */
export interface MemoryUpdate {
  id: MemoryUpdateView["id"];
  createdAt: string;
  /** 本次固化窗口最后一条消息的 created_at = 这张卡描述的线程位置（`createdAt` 只是固化
   *  跑起来的时刻，比它总结的那一轮晚）。语义 / 配额卡与旧数据为 null → 锚定退回
   *  `createdAt`（lib/memoryAnchors）。 */
  anchorAt?: MemoryUpdateView["anchor_at"];
  kind: MemoryUpdateView["kind"];
  summary?: MemoryUpdateView["summary"];
  items: MemoryUpdateItem[];
}

/** Map one OpenAPI `MemoryUpdateView` → mobile {@link MemoryUpdate}. */
export function toMemoryUpdate(u: MemoryUpdateView): MemoryUpdate {
  return {
    id: u.id,
    createdAt: u.created_at,
    anchorAt: u.anchor_at ?? null,
    kind: u.kind,
    summary: u.summary ?? null,
    items: u.items ?? [],
  };
}

/** A window of messages plus whether older ones exist (drives 加载更早). `memoryUpdates` is
 *  the conversation-tail 记忆已更新 cards — non-empty only on the latest window. */
export interface MessageWindow {
  messages: MessageDetail[];
  hasMoreBefore: boolean;
  memoryUpdates: MemoryUpdate[];
}

/** Map one OpenAPI MessageDetail row → mobile {@link MessageDetail} (incl. evidence_ledger). */
export function toMessageDetail(row: Schemas["MessageDetail"]): MessageDetail {
  const runs = row.runs;
  const status = row.status ?? null;
  // Cold-path pause latch: write keeps status=running + paused=true; hydrate as
  // paused (finish_reason=paused) so reopen does not paint forever-streaming chrome.
  const paused = Boolean(row.paused);
  const finish = paused
    ? "paused"
    : (runs?.finish_reason ?? (status === "incomplete" ? "interrupted" : null));
  return {
    id: row.id,
    role: row.role,
    content: row.content,
    reasoning_content: row.reasoning_content ?? null,
    citations: (row.citations ?? []) as Citation[],
    evidenceLedger: row.evidence_ledger?.length
      ? (row.evidence_ledger as TurnEvidenceLedgerEntry[])
      : undefined,
    runs: runs
      ? {
          events: (runs.events ?? []) as unknown as SSEEvent[],
          finish_reason: finish,
          process: (runs.process ?? null) as ProcessStep[] | null,
          captain_context: (runs.captain_context ?? null) as
            | ContextBlockWire[]
            | null,
          turn_warning: runs.turn_warning ?? null,
          error: runs.error ?? null,
          auto_folder: runs.auto_folder ?? null,
        }
      : paused
        ? {
            events: [],
            finish_reason: "paused",
            process: null,
            error: null,
          }
        : status === "incomplete"
          ? {
              events: [],
              finish_reason: "interrupted",
              process: null,
              error: null,
            }
          : null,
    status,
    paused: paused || null,
    outcome: row.outcome ?? null,
    attachments: row.attachments?.map((a) => ({
      name: a.name,
      truncated: a.truncated,
    })),
    agentMentions: row.agent_mentions?.length
      ? row.agent_mentions.map((a) => ({
          agentId: a.agent_id,
          role: a.role,
        }))
      : undefined,
    cost: row.cost ?? null,
    origin: row.origin ?? null,
    recovered: row.recovered ?? null,
    trace_id: row.trace_id ?? null,
    duration_ms: row.duration_ms ?? null,
    usage: row.usage ?? null,
    rounds: row.rounds ?? null,
    collab: row.collab ?? null,
    team_batch: row.team_batch ?? null,
    created_at: row.created_at,
  };
}

/** The latest window of a conversation's messages (chronological, oldest-first), or —
 *  with `before` (an ISO cursor) — the page strictly older than it (scroll-up). The
 *  endpoint windows at ≤200; we load 100 at a time and use `has_more_before` to know
 *  whether to keep paging back. */
export async function getMessages(
  conversationId: string,
  before?: string,
): Promise<MessageWindow> {
  const cursor = before ? `&before=${encodeURIComponent(before)}` : "";
  const res = await apiFetch(
    `/v1/conversations/${conversationId}/messages?limit=100${cursor}`,
  );
  if (!res.ok) throw new Error(`加载消息失败 (${res.status})`);
  const data = (await res.json()) as Schemas["MessageListResponse"];
  return {
    messages: data.data.map(toMessageDetail),
    hasMoreBefore: data.has_more_before,
    // Backend returns these only on the latest window; older/around pages send none.
    memoryUpdates: (data.memory_updates ?? []).map(toMemoryUpdate),
  };
}
