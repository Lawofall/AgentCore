// Document tree REST client for mobile (`/v1/documents`) — unified md entries
// under the convention root. Mirrors desktop `services/documents.ts` over
// bearer-token `apiFetch`. Scope is `folderId` (`null` = GLOBAL); the file
// rail lists entries flat by scope (no 记忆/规则 grouping, no role filter).
import { apiFetch } from "@/api/client";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];
type DocumentNodeWire = Schemas["DocumentNodeView"];
type DocumentDetailWire = Schemas["DocumentDetailView"];
type DocumentWriteWire = Schemas["DocumentWriteResult"];

/** Cloud-documents convention root name (§5.0). */
export const AGENTCORE_ROOT_NAME = "AgentCore";

/** Legacy rules directory under the convention root. */
export const RULES_DIR_NAME = "规则";

/** Legacy memory directory under the convention root (AI-maintained notes). */
export const MEMORY_DIR_NAME = "记忆";

/**
 * User-facing injection mode (§5.4). API may still store other values;
 * the mobile surface only offers these two.
 */
export type DocumentApplyMode = "always" | "on_demand";

/** Map wire `apply_mode` onto the two-state UI (unknown → on_demand per frontmatter default). */
export function toApplyMode(raw: string): DocumentApplyMode {
  return raw === "always" ? "always" : "on_demand";
}

/** A tree node's metadata (list rows — body omitted). */
export interface DocumentNode {
  id: string;
  parentId: string | null;
  /** Scope: null = GLOBAL layer, else the project (folder) this entry is bound to. */
  folderId: string | null;
  kind: "folder" | "document";
  role: "rule" | "general";
  aiMaintained: boolean;
  applyMode: DocumentApplyMode;
  /** One-line summary from frontmatter; empty is fine (not an error). */
  description: string;
  name: string;
  /** Structural frontmatter failure — entry does not inject; UI must report it. */
  frontmatterError: string | null;
  /**
   * Chars this entry contributes to the always pool (`null` when not always).
   * Matches server `always_entry_chars` so row totals == meter `usedChars`.
   */
  alwaysChars: number | null;
  /**
   * When the user marked this entry wrong (`null` = live). Disputed entries are kept
   * and still editable, but the AI stops injecting / consulting them.
   */
  disputedAt: string | null;
}

/** A node plus its markdown body + content-hash CAS tag. */
export interface DocumentDetail extends DocumentNode {
  content: string;
  version: string;
}

export interface DocumentWriteResult {
  ok: boolean;
  version: string;
  conflict: boolean;
  frontmatterError: string | null;
  quotaWarning: string | null;
}

/**
 * Always-pool usage for the UI meter.
 * `usedChars == globalChars + projectChars` (project scope includes global).
 */
export interface AlwaysQuota {
  usedChars: number;
  maxChars: number;
  percent: number;
  /** Global-scope always chars in this meter context. */
  globalChars: number;
  /** Project-scope always chars (0 when the meter is global-only). */
  projectChars: number;
}

interface AlwaysQuotaWire {
  used_chars: number;
  max_chars: number;
  percent: number;
  global_chars?: number;
  project_chars?: number;
}

/**
 * A failed documents REST call, carrying HTTP status so callers can tell a missing
 * endpoint (404/501 — 前后端版本漂移) apart from a transient failure.
 */
export class DocumentsApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "DocumentsApiError";
  }
}

/** Deployed backend lacks this endpoint (404/501) — calm「暂不可用」, don't retry. */
export function isDocumentsUnavailable(err: unknown): boolean {
  return (
    err instanceof DocumentsApiError &&
    (err.status === 404 || err.status === 501)
  );
}

async function getJson<T>(path: string, fallback: string): Promise<T> {
  const res = await apiFetch(path);
  if (!res.ok)
    throw new DocumentsApiError(res.status, `${fallback} (${res.status})`);
  return (await res.json()) as T;
}

async function sendJson<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body: unknown | undefined,
  fallback: string,
): Promise<T> {
  const res = await apiFetch(path, {
    method,
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok)
    throw new DocumentsApiError(res.status, `${fallback} (${res.status})`);
  return (await res.json()) as T;
}

const toNode = (w: DocumentNodeWire): DocumentNode => ({
  id: w.id,
  parentId: w.parent_id,
  folderId: w.folder_id,
  kind: w.kind === "folder" ? "folder" : "document",
  role: w.role === "rule" ? "rule" : "general",
  aiMaintained: w.ai_maintained,
  applyMode: toApplyMode(w.apply_mode),
  description: (w.description ?? "").trim(),
  name: w.name,
  frontmatterError: w.frontmatter_error?.trim() || null,
  alwaysChars:
    typeof w.always_chars === "number" && Number.isFinite(w.always_chars)
      ? w.always_chars
      : null,
  disputedAt: w.disputed_at?.trim() || null,
});

const toDetail = (w: DocumentDetailWire): DocumentDetail => ({
  ...toNode(w),
  content: w.content,
  version: w.version,
});

const toWriteResult = (w: DocumentWriteWire): DocumentWriteResult => ({
  ok: w.ok,
  version: w.version,
  conflict: Boolean(w.conflict),
  frontmatterError: w.frontmatter_error?.trim() || null,
  quotaWarning: w.quota_warning?.trim() || null,
});

function isUserRuleDoc(n: DocumentNode): boolean {
  return n.role === "rule" && !n.aiMaintained && n.kind === "document";
}

/** List a folder's direct children (`parentId` null = top-level). */
export function listDocuments(
  parentId: string | null = null,
): Promise<DocumentNode[]> {
  const q = parentId ? `?parent_id=${encodeURIComponent(parentId)}` : "";
  return getJson<DocumentNodeWire[]>(`/v1/documents${q}`, "加载文档失败").then(
    (rows) => rows.map(toNode),
  );
}

/**
 * Flat list of inject-able **entries** for one scope (global when `folderId` is null).
 * Walks `AgentCore/{规则,记忆}/` (and one nested level) plus leftover top-level docs.
 * Does **not** group by `role` — UI partitions by scope only.
 */
export async function listScopeEntries(
  folderId: string | null = null,
): Promise<DocumentNode[]> {
  const tops = await listDocuments(null);
  const byId = new Map<string, DocumentNode>();

  const takeDoc = (n: DocumentNode) => {
    if (n.kind === "document" && n.folderId === folderId) byId.set(n.id, n);
  };

  for (const n of tops) takeDoc(n);

  const agentcores = tops.filter(
    (n) =>
      n.kind === "folder" &&
      n.name === AGENTCORE_ROOT_NAME &&
      n.folderId === folderId,
  );

  await Promise.all(
    agentcores.map(async (ac) => {
      const kids = await listDocuments(ac.id);
      await Promise.all(
        kids.map(async (kid) => {
          if (kid.kind === "document") {
            takeDoc(kid);
            return;
          }
          if (
            kid.kind !== "folder" ||
            (kid.name !== RULES_DIR_NAME && kid.name !== MEMORY_DIR_NAME)
          ) {
            return;
          }
          const leaves = await listDocuments(kid.id);
          await Promise.all(
            leaves.map(async (leaf) => {
              if (leaf.kind === "document") {
                takeDoc(leaf);
                return;
              }
              if (leaf.kind !== "folder") return;
              const nested = await listDocuments(leaf.id);
              for (const n of nested) takeDoc(n);
            }),
          );
        }),
      );
    }),
  );

  return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name, "zh"));
}

/**
 * GLOBAL user rule documents (legacy helper). Prefer {@link listScopeEntries}
 * for the flat file-rail list. Per-project rules are omitted.
 */
export async function listUserRules(): Promise<DocumentNode[]> {
  const tops = await listDocuments(null);
  const byId = new Map<string, DocumentNode>();

  for (const n of tops) {
    if (isUserRuleDoc(n) && n.folderId === null) byId.set(n.id, n);
  }

  const agentcores = tops.filter(
    (n) =>
      n.kind === "folder" &&
      n.name === AGENTCORE_ROOT_NAME &&
      n.folderId === null,
  );
  await Promise.all(
    agentcores.map(async (ac) => {
      const kids = await listDocuments(ac.id);
      const rulesDir = kids.find(
        (k) => k.kind === "folder" && k.name === RULES_DIR_NAME,
      );
      if (!rulesDir) return;
      const rules = await listDocuments(rulesDir.id);
      for (const n of rules) {
        if (isUserRuleDoc(n) && n.folderId === null) byId.set(n.id, n);
      }
    }),
  );

  return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name, "zh"));
}

/** Always-pool usage for the injection context (global + optional project). */
export function getAlwaysQuota(
  folderId: string | null = null,
): Promise<AlwaysQuota> {
  const q =
    folderId != null ? `?folder_id=${encodeURIComponent(folderId)}` : "";
  return getJson<AlwaysQuotaWire>(
    `/v1/documents/always-quota${q}`,
    "加载常驻配额失败",
  ).then((w) => {
    const globalChars =
      typeof w.global_chars === "number" && Number.isFinite(w.global_chars)
        ? w.global_chars
        : folderId == null
          ? w.used_chars
          : 0;
    const projectChars =
      typeof w.project_chars === "number" && Number.isFinite(w.project_chars)
        ? w.project_chars
        : folderId != null
          ? Math.max(0, w.used_chars - globalChars)
          : 0;
    return {
      usedChars: w.used_chars,
      maxChars: w.max_chars,
      percent: w.percent,
      globalChars,
      projectChars,
    };
  });
}

/** Load one document's body + CAS version. */
export function getDocument(id: string): Promise<DocumentDetail> {
  return getJson<DocumentDetailWire>(
    `/v1/documents/${encodeURIComponent(id)}`,
    "加载规则失败",
  ).then(toDetail);
}

/**
 * Create a GLOBAL user rule (`role=rule`, default `apply_mode=always`).
 * With `parent_id=null` the API auto-parents under `AgentCore/规则/` (§5.0).
 */
export function createRuleDocument(
  name: string,
  content = "",
): Promise<DocumentDetail> {
  return sendJson<DocumentDetailWire>(
    "/v1/documents",
    "POST",
    {
      name,
      kind: "document",
      role: "rule",
      content,
      parent_id: null,
      folder_id: null,
      apply_mode: "always",
    } satisfies Schemas["DocumentCreateRequest"],
    "新建规则失败",
  ).then(toDetail);
}

/** Switch a rule's injection mode (`always` ↔ `on_demand`). */
export function updateDocumentApplyMode(
  id: string,
  applyMode: DocumentApplyMode,
): Promise<DocumentNode> {
  return sendJson<DocumentDetailWire>(
    `/v1/documents/${encodeURIComponent(id)}`,
    "PATCH",
    {
      apply_mode: applyMode,
      reparent: false,
    } satisfies Schemas["DocumentPatchRequest"],
    "切换应用方式失败",
  ).then(toNode);
}

/** Overwrite a document's body (full-text, CAS-guarded). */
export function writeDocument(
  id: string,
  content: string,
  baseline: string | null,
): Promise<DocumentWriteResult> {
  return sendJson<DocumentWriteWire>(
    `/v1/documents/${encodeURIComponent(id)}`,
    "PUT",
    { content, baseline },
    "保存规则失败",
  ).then(toWriteResult);
}

/** Rename a document (content untouched). */
export function renameDocument(
  id: string,
  name: string,
): Promise<DocumentNode> {
  return sendJson<DocumentDetailWire>(
    `/v1/documents/${encodeURIComponent(id)}`,
    "PATCH",
    {
      name,
      reparent: false,
    } satisfies Schemas["DocumentPatchRequest"],
    "重命名失败",
  ).then(toNode);
}

/** Soft-delete a document. */
export function deleteDocument(id: string): Promise<DocumentWriteResult> {
  return sendJson<DocumentWriteWire>(
    `/v1/documents/${encodeURIComponent(id)}`,
    "DELETE",
    undefined,
    "删除规则失败",
  ).then(toWriteResult);
}
