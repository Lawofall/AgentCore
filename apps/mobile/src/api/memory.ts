// Long-term AI memory REST client for mobile (Agent记忆与知识系统 §一).
//
// Mirrors the desktop `services/memory.ts` contract, but over the bearer-token
// `apiFetch` (mobile has no cookie origin). 精简版 (手机端): GLOBAL scope only — no
// per-project layer, no AI 改写 / preview. The phone is a 查看 + 改 + 删 lens on the
// always-injected core (偏好 全局 + 画像 全局) and the on-demand 主题 notes, plus the
// cross-conversation「最近更新」feed. The contract mirrors the workspace edit contract:
// full text + a content-addressed `version` baseline the next write does its CAS against.
// REST wire DTOs track OpenAPI via @agentcore/contract-rest-types.
import { apiFetch } from "@/api/client";
import type { MemoryUpdateItem } from "@/api/conversations";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

export type MemoryDoc = Schemas["MemoryResponse"];
export type MemoryFileDoc = Schemas["MemoryFileResponse"];
export type MemoryWriteResult = Schemas["MemoryWriteResult"];
export type MemoryKind = Schemas["MemoryKind"];

/**
 * A failed memory REST call, carrying the HTTP status so callers can tell a missing
 * endpoint (404/501 — this deployed backend predates the feature) apart from a
 * transient failure. Still a plain Error subclass, so existing catch sites that only
 * read `.message` are unaffected.
 */
export class MemoryApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "MemoryApiError";
  }
}

/**
 * Whether an error means the deployed backend lacks this endpoint (404/501) — the
 * 前后端版本漂移 window (a newer client calling an endpoint an older *deployed* backend
 * lacks, e.g. 记忆·主题 shipped in the client before the backend redeploy). Retrying
 * can't fix it, so the caller degrades to a calm "暂不可用" note (no red error).
 */
export function isFeatureUnavailable(err: unknown): boolean {
  return (
    err instanceof MemoryApiError && (err.status === 404 || err.status === 501)
  );
}

async function getJson<T>(path: string, fallback: string): Promise<T> {
  const res = await apiFetch(path);
  if (!res.ok)
    throw new MemoryApiError(res.status, `${fallback} (${res.status})`);
  return (await res.json()) as T;
}

async function putJson<T>(
  path: string,
  body: unknown,
  fallback: string,
): Promise<T> {
  const res = await apiFetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok)
    throw new MemoryApiError(res.status, `${fallback} (${res.status})`);
  return (await res.json()) as T;
}

/** Load the memory document (whole-doc body + version; content API retained for callers). */
export function getMemory(): Promise<MemoryDoc> {
  return getJson<MemoryDoc>("/v1/users/me/memory", "加载记忆失败");
}

/** Load one always-injected core leaf (偏好 / 画像), GLOBAL layer. */
export function getMemoryFile(kind: MemoryKind): Promise<MemoryFileDoc> {
  return getJson<MemoryFileDoc>(
    `/v1/users/me/memory/files/${kind}`,
    "加载失败",
  );
}

/**
 * Write one core leaf back (full-text, CAS-guarded). Empty `content` clears the leaf.
 * A `baseline` that no longer matches returns `{ ok: false, conflict: true }`.
 */
export function writeMemoryFile(
  kind: MemoryKind,
  content: string,
  baseline: string | null,
): Promise<MemoryWriteResult> {
  return putJson<MemoryWriteResult>(
    `/v1/users/me/memory/files/${kind}`,
    { content, baseline } satisfies Schemas["MemoryWriteRequest"],
    "保存失败",
  );
}

/** GLOBAL 主题 note slugs (on-demand notes the agent pulls via consult). */
export function listMemoryTopics(): Promise<string[]> {
  return getJson<Schemas["MemoryTopicsResponse"]>(
    "/v1/users/me/memory/topics",
    "加载主题失败",
  ).then((r) => r.topics);
}

/** Load one GLOBAL 主题 note's body (+ CAS version). */
export function getMemoryTopic(slug: string): Promise<MemoryFileDoc> {
  return getJson<MemoryFileDoc>(
    `/v1/users/me/memory/topics/${encodeURIComponent(slug)}`,
    "加载主题失败",
  );
}

/**
 * Write one 主题 note back (full-text, CAS-guarded). Empty `content` deletes the note.
 * A `baseline` that no longer matches returns `{ ok: false, conflict: true }`.
 */
export function writeMemoryTopic(
  slug: string,
  content: string,
  baseline: string | null,
): Promise<MemoryWriteResult> {
  return putJson<MemoryWriteResult>(
    `/v1/users/me/memory/topics/${encodeURIComponent(slug)}`,
    { content, baseline } satisfies Schemas["MemoryWriteRequest"],
    "保存失败",
  );
}

type MemoryUpdateFeedItem = Schemas["MemoryUpdateFeedItem"];

/**
 * One offline-consolidation pass in the cross-conversation「最近更新」feed (§1.6).
 * CamelCase client projection of OpenAPI `MemoryUpdateFeedItem` (M17 exemption:
 * OpenAPI has no camelCase feed-entry schema).
 *
 *  `kind` is the generated wire type (not a narrower local union). Pass it through
 *  unchanged so a new value cannot be silently rewritten to `"semantic"`.
 */
export interface MemoryUpdateFeedEntry {
  id: MemoryUpdateFeedItem["id"];
  conversationId: string;
  createdAt: string;
  kind: MemoryUpdateFeedItem["kind"];
  summary?: MemoryUpdateFeedItem["summary"];
  items: MemoryUpdateItem[];
}

/** Map one OpenAPI `MemoryUpdateFeedItem` → mobile {@link MemoryUpdateFeedEntry}. */
export function toMemoryUpdateFeedEntry(
  u: MemoryUpdateFeedItem,
): MemoryUpdateFeedEntry {
  return {
    id: u.id,
    conversationId: u.conversation_id,
    createdAt: u.created_at,
    kind: u.kind,
    summary: u.summary ?? null,
    items: u.items ?? [],
  };
}

/**
 * The signed-in user's recent memory updates across ALL conversations, newest-first
 * (记忆更新对话内可见 §1.6 — write-side home on `/memory`). `limit` caps how many
 * recent passes to pull.
 */
export async function listMemoryUpdates(
  limit = 30,
): Promise<MemoryUpdateFeedEntry[]> {
  const data = await getJson<Schemas["MemoryUpdatesFeedResponse"]>(
    `/v1/users/me/memory/updates?limit=${limit}`,
    "加载记忆更新失败",
  );
  return (data.updates ?? []).map(toMemoryUpdateFeedEntry);
}
