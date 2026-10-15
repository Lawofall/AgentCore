import { api } from "@/services/api";
import { scheduleAccountRulesMemoryRefresh } from "@/services/refreshAccountRulesMemory";

/**
 * Document tree REST client (`/v1/documents`) — unified md **entries** under the
 * convention root (Agent记忆与知识系统 · 目标形态「统一 md 条目基座」).
 *
 * Nodes are addressed by **id**. Scope is `folderId` (`null` = GLOBAL). The file rail
 * lists entries flat by scope (no 记忆/规则/文档 folders). `apply_mode` / `description`
 * are derived indexes of body frontmatter; the UI never shows the raw `apply` key,
 * and no longer paints 常驻/按需 badges — location is the 加载档. `frontmatter_error`
 * means the entry does not inject — surface it.
 *
 * Write-side always cap (file rail does not meter remaining chars). Editing an
 * existing always entry past the cap returns `quota_warning`; create / promote
 * past the cap is 409 `ALWAYS_QUOTA_EXCEEDED`. {@link getAlwaysQuota} is the
 * internal usage endpoint, not a product dashboard.
 *
 * 纠错通道 API 仍可标 ``disputed``（停注入、留盘）；产品面不提供停用 / 恢复，不要了就删。
 */

/** Cloud-documents convention root name. ≠ local disk `~/Documents/GAgent`. */
export const AGENTCORE_ROOT_NAME = "GAgent";

/** Leftover document-tree roots. New markdown is not parented under these. */
const CONVENTION_ROOT_NAMES = new Set([AGENTCORE_ROOT_NAME, "AgentCore"]);

/** Leftover rules folder name under a convention root. Not created for new documents. */
export const RULES_DIR_NAME = "rules";

/** 每次都带上 / 自己判断何时翻开 / 碰到这类文件就带上. */
export type DocumentApplyMode = "always" | "on_demand" | "paths";

/** Map wire `apply_mode`. Unknown values stay 按需 (frontmatter's missing-key default). */
export function toApplyMode(raw: string): DocumentApplyMode {
  if (raw === "always" || raw === "paths") return raw;
  return "on_demand";
}

/** A tree node's metadata (list rows — body omitted so a listing stays light). */
export interface DocumentNode {
  id: string;
  parentId: string | null;
  /** Scope: null = GLOBAL layer, else the project (folder) this entry is bound to. */
  folderId: string | null;
  kind: "folder" | "document";
  role: "rule" | "general";
  /** true = AI-maintained (write-side / UI review); false = user-owned. */
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
   * When the user marked this entry wrong (`null` = live). Disputed entries are kept and
   * still editable here, but the AI stops injecting / consulting them (纠错通道).
   */
  disputedAt: string | null;
}

/** A node plus its markdown body + content-hash CAS tag (the editor's load payload). */
export interface DocumentDetail extends DocumentNode {
  content: string;
  version: string;
  /** Soft warning when a user edit of an existing always entry exceeds the pool. */
  quotaWarning: string | null;
}

export interface DocumentWriteResult {
  ok: boolean;
  /** Content-addressed CAS tag; sent back as the next write's baseline (stale → conflict). */
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

interface DocumentNodeWire {
  id: string;
  parent_id: string | null;
  folder_id: string | null;
  kind: string;
  role: string;
  ai_maintained: boolean;
  apply_mode: string;
  description?: string | null;
  name: string;
  frontmatter_error?: string | null;
  always_chars?: number | null;
  disputed_at?: string | null;
}

interface DocumentDetailWire extends DocumentNodeWire {
  content: string;
  version: string;
  quota_warning?: string | null;
}

interface DocumentWriteWire {
  ok: boolean;
  version: string;
  conflict?: boolean;
  frontmatter_error?: string | null;
  quota_warning?: string | null;
}

interface AlwaysQuotaWire {
  used_chars: number;
  max_chars: number;
  percent: number;
  global_chars?: number;
  project_chars?: number;
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
  quotaWarning: w.quota_warning?.trim() || null,
});

const toWriteResult = (w: DocumentWriteWire): DocumentWriteResult => ({
  ok: w.ok,
  version: w.version,
  conflict: Boolean(w.conflict),
  frontmatterError: w.frontmatter_error?.trim() || null,
  quotaWarning: w.quota_warning?.trim() || null,
});

/** List a folder's direct children (`parentId` null = the user's top-level nodes). */
export function listDocuments(
  parentId: string | null = null,
): Promise<DocumentNode[]> {
  const q = parentId ? `?parent_id=${encodeURIComponent(parentId)}` : "";
  return api
    .get<DocumentNodeWire[]>(`/v1/documents${q}`)
    .then((rows) => rows.map(toNode));
}

/**
 * Flat list of markdown entries for one scope (global when `folderId` is null).
 * Top-level documents, plus leftovers still sitting under a convention `rules/` folder.
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
      CONVENTION_ROOT_NAMES.has(n.name) &&
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
          if (kid.kind !== "folder" || kid.name !== RULES_DIR_NAME) {
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

/** Account markdown: top-level entries, plus leftovers under a convention `rules/` folder. */
export async function listAccountPromptTree(): Promise<{
  rulesDirId: string | null;
  folders: DocumentNode[];
  documents: DocumentNode[];
}> {
  const tops = await listDocuments(null);
  const folders: DocumentNode[] = [];
  const documents: DocumentNode[] = [];
  const seen = new Set<string>();
  const take = (node: DocumentNode, bucket: DocumentNode[]) => {
    if (seen.has(node.id)) return;
    seen.add(node.id);
    bucket.push(node);
  };
  for (const node of tops) {
    if (node.folderId != null || node.role !== "rule") continue;
    if (node.kind === "folder") take(node, folders);
    else if (node.kind === "document") take(node, documents);
  }
  const agentcores = tops.filter(
    (n) =>
      n.kind === "folder" &&
      CONVENTION_ROOT_NAMES.has(n.name) &&
      n.folderId == null,
  );
  let rulesDirId: string | null = null;
  for (const ac of agentcores) {
    const kids = await listDocuments(ac.id);
    const rulesDir = kids.find(
      (k) => k.kind === "folder" && k.name === RULES_DIR_NAME,
    );
    if (!rulesDir) continue;
    rulesDirId = rulesDir.id;
    const children = await listDocuments(rulesDir.id);
    for (const child of children) {
      if (child.kind === "folder") {
        take(child, folders);
        const nested = await listDocuments(child.id);
        for (const leaf of nested) {
          if (leaf.kind === "document") take(leaf, documents);
        }
      } else if (child.kind === "document") {
        take(child, documents);
      }
    }
  }
  return { rulesDirId, folders, documents };
}

/**
 * All user-owned rule documents across scopes (legacy helper). Prefer
 * {@link listScopeEntries} for the flat file-rail list.
 */
export async function listUserRules(): Promise<DocumentNode[]> {
  const tops = await listDocuments(null);
  const byId = new Map<string, DocumentNode>();
  const isUserRule = (n: DocumentNode) =>
    n.role === "rule" && !n.aiMaintained && n.kind === "document";

  for (const n of tops) {
    if (isUserRule(n)) byId.set(n.id, n);
  }

  const agentcores = tops.filter(
    (n) => n.kind === "folder" && CONVENTION_ROOT_NAMES.has(n.name),
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
        if (isUserRule(n)) byId.set(n.id, n);
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
  return api
    .get<AlwaysQuotaWire>(`/v1/documents/always-quota${q}`)
    .then((w) => {
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

/** Load one document's body + CAS version (the editor's load). */
export function getDocument(id: string): Promise<DocumentDetail> {
  return api
    .get<DocumentDetailWire>(`/v1/documents/${encodeURIComponent(id)}`)
    .then(toDetail);
}

/**
 * Create a user-owned markdown document in a scope (`folderId` null = global).
 */
export function createRuleDocument(
  name: string,
  folderId: string | null = null,
  content = "",
  applyMode: DocumentApplyMode = "always",
  parentId: string | null = null,
): Promise<DocumentDetail> {
  return api
    .post<DocumentDetailWire>("/v1/documents", {
      name,
      kind: "document",
      role: "rule",
      content,
      parent_id: parentId,
      folder_id: folderId,
      apply_mode: applyMode,
    })
    .then(toDetail)
    .then((doc) => {
      scheduleAccountRulesMemoryRefresh();
      return doc;
    });
}

/** User-made folder of markdown documents (`role=rule` + `kind=folder`). */
export function createRuleFolder(name: string): Promise<DocumentNode> {
  return api
    .post<DocumentNodeWire>("/v1/documents", {
      name,
      kind: "folder",
      role: "rule",
      parent_id: null,
    })
    .then(toNode)
    .then((node) => {
      scheduleAccountRulesMemoryRefresh();
      return node;
    });
}

/** Move a rule document. Passing `applyMode` keeps location and 加载档 in lockstep. */
export function reparentDocument(
  id: string,
  parentId: string | null,
  applyMode?: DocumentApplyMode,
): Promise<DocumentNode> {
  return api
    .patch<DocumentNodeWire>(`/v1/documents/${encodeURIComponent(id)}`, {
      parent_id: parentId,
      reparent: true,
      ...(applyMode ? { apply_mode: applyMode } : {}),
    })
    .then(toNode)
    .then((node) => {
      scheduleAccountRulesMemoryRefresh();
      return node;
    });
}

/** Switch an entry's injection mode (`always` ↔ `on_demand`). */
export function updateDocumentApplyMode(
  id: string,
  applyMode: DocumentApplyMode,
): Promise<DocumentNode> {
  return api
    .patch<DocumentNodeWire>(`/v1/documents/${encodeURIComponent(id)}`, {
      apply_mode: applyMode,
    })
    .then(toNode)
    .then((node) => {
      scheduleAccountRulesMemoryRefresh();
      return node;
    });
}

/**
 * Mark / unmark an entry as disputed (API leftover; 产品面不提供停用入口).
 *
 * Explicit caller only — nothing here is inferred from conversation text.
 * A disputed entry stops being injected and drops off the AI on-demand catalog.
 * The row stays readable until the user deletes it.
 */
export function setDocumentDisputed(
  id: string,
  disputed: boolean,
): Promise<DocumentNode> {
  return api
    .patch<DocumentNodeWire>(`/v1/documents/${encodeURIComponent(id)}`, {
      disputed,
    })
    .then(toNode)
    .then((node) => {
      scheduleAccountRulesMemoryRefresh();
      return node;
    });
}

/**
 * Overwrite a document's body (full-text, CAS-guarded). `baseline` is the version the edit
 * was based on; `null` writes unconditionally. A stale baseline returns
 * `{ ok: false, conflict: true }` with the live version.
 */
export function writeDocument(
  id: string,
  content: string,
  baseline: string | null,
): Promise<DocumentWriteResult> {
  return api
    .put<DocumentWriteWire>(`/v1/documents/${encodeURIComponent(id)}`, {
      content,
      baseline,
    })
    .then(toWriteResult)
    .then((result) => {
      if (result.ok && !result.conflict) scheduleAccountRulesMemoryRefresh();
      return result;
    });
}

/** Rename a document (content untouched). */
export function renameDocument(
  id: string,
  name: string,
): Promise<DocumentNode> {
  return api
    .patch<DocumentNodeWire>(`/v1/documents/${encodeURIComponent(id)}`, {
      name,
    })
    .then(toNode)
    .then((node) => {
      scheduleAccountRulesMemoryRefresh();
      return node;
    });
}

/** Soft-delete a document (and, for a folder, its subtree). */
export function deleteDocument(id: string): Promise<DocumentWriteResult> {
  return api
    .delete<DocumentWriteWire>(`/v1/documents/${encodeURIComponent(id)}`)
    .then(toWriteResult)
    .then((result) => {
      if (result.ok && !result.conflict) scheduleAccountRulesMemoryRefresh();
      return result;
    });
}
