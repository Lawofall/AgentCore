import { api } from "@/services/api";
import type { MemoryUpdateItem } from "@/stores/conversation";
import type { components } from "@/types/api.generated";

type MemoryUpdateFeedItemWire = components["schemas"]["MemoryUpdateFeedItem"];

/**
 * Long-term AI memory REST client (`/v1/users/me/memory`).
 *
 * The user's memory is the markdown body of their `ai_maintained` rule file
 * (Agent记忆与知识系统). It is edited through the same source-agnostic markdown editor
 * the file workbench uses (see `services/sources/memorySource`), so the contract
 * mirrors the workspace edit contract: full text + a content-addressed `version`
 * baseline the next write does its CAS against.
 */

export interface MemoryDoc {
  content: string;
  /** Content-addressed CAS tag; sent back as the write baseline (stale → conflict). */
  version: string;
  enabled: boolean;
}

export interface MemoryWriteResult {
  ok: boolean;
  version: string;
  conflict: boolean;
}

/** Load the memory document (whole-doc body + version; content APIs for callers). */
export function getMemory(): Promise<MemoryDoc> {
  return api.get<MemoryDoc>("/v1/users/me/memory");
}

/**
 * Write the memory body back (full-document edit). `baseline` is the version the
 * edit was based on; `null` writes unconditionally (清空 / 仍然覆盖). A baseline that
 * no longer matches returns `{ ok: false, conflict: true }` with the live version.
 */
export function writeMemory(
  content: string,
  baseline: string | null,
): Promise<MemoryWriteResult> {
  return api.put<MemoryWriteResult>("/v1/users/me/memory", {
    content,
    baseline,
  });
}

/**
 * A single memory *leaf* (Agent记忆与知识系统 §1.4). The always-injected core is split into
 * 偏好 (`preferences`, GLOBAL-only) + 画像 (`profile`, global or per-project) + 导航
 * (`navigation`, PROJECT-only), each its own editable file. `profile` / `navigation` take
 * an optional `folderId` to address a project's layer (`navigation` requires it).
 */
export type MemoryKind = "preferences" | "profile" | "navigation";

export interface MemoryFileDoc {
  content: string;
  /** Per-file content hash; sent back as the write baseline (stale → conflict). */
  version: string;
}

const memoryFilePath = (kind: MemoryKind, folderId: string | null): string =>
  folderId
    ? `/v1/users/me/memory/files/${kind}?folder_id=${encodeURIComponent(folderId)}`
    : `/v1/users/me/memory/files/${kind}`;

/** Load one memory leaf — 偏好/画像 (global), a project's 画像, or a project's 导航. */
export function getMemoryFile(
  kind: MemoryKind,
  folderId: string | null = null,
): Promise<MemoryFileDoc> {
  return api.get<MemoryFileDoc>(memoryFilePath(kind, folderId));
}

/**
 * Write one memory leaf back (full-text, CAS-guarded). Empty `content` clears (drops) the
 * leaf. A `baseline` that no longer matches returns `{ ok: false, conflict: true }`.
 */
export function writeMemoryFile(
  kind: MemoryKind,
  content: string,
  baseline: string | null,
  folderId: string | null = null,
): Promise<MemoryWriteResult> {
  return api.put<MemoryWriteResult>(memoryFilePath(kind, folderId), {
    content,
    baseline,
  });
}

/**
 * One offline-consolidation pass in the cross-conversation「记忆动态」feed (记忆编辑器
 * 「最近更新」视图). Same applied-change items the in-conversation card shows, plus the
 * `conversationId` it came from so the feed can link back to that thread.
 */
export interface MemoryUpdateFeedEntry {
  id: string;
  conversationId: string;
  createdAt: string;
  /** `quota` = the always pool refused a write (审计 CTX-A2), not an applied change. */
  kind: components["schemas"]["MemoryUpdateView"]["kind"];
  summary?: string | null;
  items: MemoryUpdateItem[];
}

/**
 * The signed-in user's recent memory updates across ALL conversations, newest-first
 * (记忆更新对话内可见 §1.6 — the write side's cross-conversation home). Powers the files-page
 * 「最近更新」view; `limit` caps how many recent passes to pull.
 */
export function listMemoryUpdates(
  limit = 50,
): Promise<MemoryUpdateFeedEntry[]> {
  return api
    .get<{
      updates: MemoryUpdateFeedItemWire[];
    }>(`/v1/users/me/memory/updates?limit=${limit}`)
    .then((r) =>
      r.updates.map((u) => ({
        id: u.id,
        conversationId: u.conversation_id,
        createdAt: u.created_at,
        kind: u.kind,
        summary: u.summary ?? null,
        items: (u.items ?? []).map(
          (it): MemoryUpdateItem => ({
            action: it.action,
            file: it.file,
            section: it.section,
            scope: it.scope,
            content: it.content,
            target: it.target,
            projectId: it.project_id ?? null,
          }),
        ),
      })),
    );
}

/** folder_ids that have project-scoped memory — retained for API callers / diagnostics;
 * the file rail no longer aggregates them under a top-level「项目记忆」folder (each
 * folder mounts its own ``.agentcore`` child instead). */
export function listMemoryProjects(): Promise<string[]> {
  return api
    .get<{ folders: string[] }>("/v1/users/me/memory/projects")
    .then((r) => r.folders);
}

/**
 * On-demand TOPIC notes (``主题/<slug>.md``) live alongside the always-injected core: the
 * agent pulls them via `consult`, and the「文件」rail's 主题/ folder browses them.
 * `folderId` null = the GLOBAL 主题/ folder, else that project's (same scope convention as
 * the per-leaf surface). Names only ride the listing; a note's body is pulled per-open.
 */
export function listMemoryTopics(
  folderId: string | null = null,
): Promise<string[]> {
  const q = folderId ? `?folder_id=${encodeURIComponent(folderId)}` : "";
  return api
    .get<{ topics: string[] }>(`/v1/users/me/memory/topics${q}`)
    .then((r) => r.topics);
}

const memoryTopicApiPath = (slug: string, folderId: string | null): string => {
  const base = `/v1/users/me/memory/topics/${encodeURIComponent(slug)}`;
  return folderId ? `${base}?folder_id=${encodeURIComponent(folderId)}` : base;
};

/** Load one TOPIC note's body (+ CAS version), in the global or a project's 主题/ folder. */
export function getMemoryTopic(
  slug: string,
  folderId: string | null = null,
): Promise<MemoryFileDoc> {
  return api.get<MemoryFileDoc>(memoryTopicApiPath(slug, folderId));
}

/**
 * Write one TOPIC note back (full-text, CAS-guarded). Empty `content` clears (drops) the
 * note. A `baseline` that no longer matches returns `{ ok: false, conflict: true }`.
 */
export function writeMemoryTopic(
  slug: string,
  content: string,
  baseline: string | null,
  folderId: string | null = null,
): Promise<MemoryWriteResult> {
  return api.put<MemoryWriteResult>(memoryTopicApiPath(slug, folderId), {
    content,
    baseline,
  });
}

/** Direction for {@link moveMemoryBullet} (位置即作用域纠错). */
export type MemoryMoveDirection = "to_project" | "to_global";

export type MemoryMoveKind = "preferences" | "profile" | "topic";

export interface MemoryMoveBulletInput {
  content: string;
  section: string;
  folderId: string;
  direction: MemoryMoveDirection;
  kind?: MemoryMoveKind;
  topicSlug?: string | null;
  sourceBaseline?: string | null;
  targetBaseline?: string | null;
}

export interface MemoryMoveBulletResult {
  ok: boolean;
  conflict: boolean;
  sourceVersion: string;
  targetVersion: string;
  message?: string | null;
}

/**
 * Move one bullet between GLOBAL and the current project layer (remove + add under
 * the same section). Illegal sections (偏好 / 纠正记录 → project, 项目约束 → global)
 * return HTTP 422 with a clear message.
 */
export function moveMemoryBullet(
  input: MemoryMoveBulletInput,
): Promise<MemoryMoveBulletResult> {
  return api
    .post<{
      ok: boolean;
      conflict: boolean;
      source_version: string;
      target_version: string;
      message?: string | null;
    }>("/v1/users/me/memory/move-bullet", {
      content: input.content,
      section: input.section,
      folder_id: input.folderId,
      direction: input.direction,
      kind: input.kind ?? "profile",
      topic_slug: input.topicSlug ?? null,
      source_baseline: input.sourceBaseline ?? null,
      target_baseline: input.targetBaseline ?? null,
    })
    .then((r) => ({
      ok: r.ok,
      conflict: r.conflict,
      sourceVersion: r.source_version,
      targetVersion: r.target_version,
      message: r.message ?? null,
    }));
}

/**
 * React Query keys of the two surfaces a 行级异议 changes, so every entry point can
 * invalidate the ones it is not rendering (the「记忆已更新」card rejects a line that the
 * 记忆动态 view is what shows and undoes).
 */
export const MEMORY_UPDATES_KEY = ["memory-updates"];
export const MEMORY_DISPUTED_LINES_KEY = ["memory-disputed-lines"];

export interface MemoryDisputeLineInput {
  content: string;
  section: string;
  /** Omitted / null = the global layer (unlike a move, which needs a project). */
  folderId?: string | null;
  kind?: MemoryMoveKind;
  topicSlug?: string | null;
  baseline?: string | null;
}

export interface MemoryDisputeLineResult {
  ok: boolean;
  conflict: boolean;
  version: string;
  /**
   * Id of the record just written — the handle {@link restoreMemoryLine} takes; empty when
   * there is nothing to undo. Never a position: rejecting several lines and undoing an
   * earlier one shifts the rest, so an index would put back somebody else's sentence.
   */
  lineId: string;
}

/**
 * Reject ONE bullet the user was shown (纠错通道·行级「这条不对」).
 *
 * The line leaves the entry body — the rest of the entry keeps working. This is the
 * sentence-level counterpart to the entry-level `disputed` flag on the documents API,
 * which silences a whole file. Undo via {@link restoreMemoryLine}.
 */
export function disputeMemoryLine(
  input: MemoryDisputeLineInput,
): Promise<MemoryDisputeLineResult> {
  return api
    .post<{
      ok: boolean;
      conflict: boolean;
      version: string;
      line_id: string;
    }>("/v1/users/me/memory/dispute-line", {
      content: input.content,
      section: input.section,
      folder_id: input.folderId ?? null,
      kind: input.kind ?? "profile",
      topic_slug: input.topicSlug ?? null,
      baseline: input.baseline ?? null,
    })
    .then((r) => ({
      ok: r.ok,
      conflict: r.conflict,
      version: r.version,
      lineId: r.line_id,
    }));
}

export interface MemoryRestoreLineInput {
  /** Record id from {@link disputeMemoryLine} / {@link listDisputedMemoryLines}. */
  id: string;
  folderId?: string | null;
  kind?: MemoryMoveKind;
  topicSlug?: string | null;
}

/**
 * Undo one line-level rejection — the bullet goes back into its entry.
 *
 * An id the server no longer holds is a 422, not a best-effort restore of some other
 * record: putting back a line the user did not name would be worse than doing nothing.
 */
export function restoreMemoryLine(
  input: MemoryRestoreLineInput,
): Promise<MemoryDisputeLineResult> {
  return api
    .post<{
      ok: boolean;
      conflict: boolean;
      version: string;
    }>("/v1/users/me/memory/restore-line", {
      id: input.id,
      folder_id: input.folderId ?? null,
      kind: input.kind ?? "profile",
      topic_slug: input.topicSlug ?? null,
    })
    .then((r) => ({
      ok: r.ok,
      conflict: r.conflict,
      version: r.version,
      lineId: "",
    }));
}

export interface MemoryDisputedLine {
  kind: MemoryMoveKind;
  topicSlug: string | null;
  folderId: string | null;
  id: string;
  section: string;
  text: string;
  disputedAt: string;
}

export interface MemoryDisputedLines {
  lines: MemoryDisputedLine[];
  /** How many records ONE entry keeps before the oldest stops being restorable. */
  maxPerEntry: number;
}

/**
 * Bullets the user rejected in one scope, newest state as stored.
 *
 * A rejected line is gone from the entry body, so this is the only place it can be read
 * back from — without it a mis-click would be unrecoverable.
 */
export function listDisputedMemoryLines(
  folderId?: string | null,
): Promise<MemoryDisputedLines> {
  const query = folderId ? `?folder_id=${encodeURIComponent(folderId)}` : "";
  return api
    .get<{
      lines: Array<{
        kind: MemoryMoveKind;
        topic_slug: string | null;
        folder_id: string | null;
        id: string;
        section: string;
        text: string;
        disputed_at: string;
      }>;
      max_per_entry: number;
    }>(`/v1/users/me/memory/disputed-lines${query}`)
    .then((r) => ({
      lines: r.lines.map((l) => ({
        kind: l.kind,
        topicSlug: l.topic_slug,
        folderId: l.folder_id,
        id: l.id,
        section: l.section,
        text: l.text,
        disputedAt: l.disputed_at,
      })),
      maxPerEntry: r.max_per_entry,
    }));
}

/**
 * Empty the rejected-line list (「已移走的记忆」的清空入口).
 *
 * The lines stay rejected — only the ability to put them back goes, which is why the UI
 * confirms first. Returns how many entries had records dropped.
 */
export function clearDisputedMemoryLines(): Promise<number> {
  return api
    .delete<{ cleared_entries: number }>("/v1/users/me/memory/disputed-lines")
    .then((r) => r.cleared_entries);
}
