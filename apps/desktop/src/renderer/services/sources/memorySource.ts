import type { FileNode, FilePreviewResult, FileSource } from "@/lib/fileSource";
import {
  type MemoryKind,
  getMemoryFile,
  getMemoryTopic,
  writeMemoryFile,
  writeMemoryTopic,
} from "@/services/memory";

/**
 * A {@link FileSource} over the user's long-term-memory **leaves**, so the files-page
 * 设定 rail can reuse the same markdown editor host ({@link MarkdownFileEditor}) the file workbench
 * uses — full-text edit + preview + AI 改写 + CAS conflict handling, all for free
 * (Agent记忆与知识系统 §1.6).
 *
 * Agent记忆与知识系统 §1.4: there is no longer ONE memory doc — the always-injected core is
 * split into 偏好 (global) + 画像 (global or per-project) + 导航 (project-only). Each leaf
 * is one editable virtual file addressed by a synthetic PATH that encodes (kind, scope):
 *
 *   global/preferences          → 偏好.md (global)
 *   global/profile              → 画像.md (global)
 *   project/<folderId>/profile  → 画像.md (that project's layer)
 *   project/<folderId>/navigation → 导航.md (that project's short entry — PROJECT-only)
 *   global/topics/<slug>        → 主题/<slug>.md (global on-demand note)
 *   project/<folderId>/topics/<slug> → 主题/<slug>.md (that project's on-demand note)
 *
 * The source is path-aware (the editor passes each tab's path to every call), so ONE
 * instance serves all leaves. tree / CRUD are never reached (the editor only calls
 * `readForEdit` / `writeText`; the rail lists topics directly via `services/memory`), so
 * they reject rather than pretend. `version.etag` carries the per-file content hash — the
 * editor sends it back as the write baseline, so an offline consolidation that moved a leaf
 * underneath surfaces as a conflict, never a silent clobber.
 */

/** Synthetic tab path for the cross-conversation「记忆动态 / 最近更新」feed view — NOT a
 * memory leaf (the workbench renders {@link MemoryUpdatesView} for it instead of the file
 * editor). Kept distinct from the `global/…` · `project/…` leaf scheme so it never parses
 * as a leaf. */
export const MEMORY_UPDATES_PATH = "__memory_updates__";

/** The synthetic leaf path for the GLOBAL 偏好 (沟通/工作习惯). */
export const GLOBAL_PREFERENCES_PATH = "global/preferences";
/** The synthetic leaf path for the GLOBAL 画像 (技术栈/关于用户的事实). */
export const GLOBAL_PROFILE_PATH = "global/profile";

/** The synthetic leaf path for a project's 画像 (scope = its folderId). */
export function memoryProjectProfilePath(folderId: string): string {
  return `project/${folderId}/profile`;
}

/** The synthetic leaf path for a project's 导航 (short entry; PROJECT-only). */
export function memoryProjectNavigationPath(folderId: string): string {
  return `project/${folderId}/navigation`;
}

/** The synthetic leaf path for an on-demand TOPIC note (global when `folderId` is null). */
export function memoryTopicPath(folderId: string | null, slug: string): string {
  return folderId
    ? `project/${folderId}/topics/${slug}`
    : `global/topics/${slug}`;
}

type MemoryLeaf =
  | { kind: MemoryKind; folderId: string | null; slug?: undefined }
  | { kind: "topic"; folderId: string | null; slug: string };

const PROJECT_PROFILE_RE = /^project\/([^/]+)\/profile$/;
const PROJECT_NAVIGATION_RE = /^project\/([^/]+)\/navigation$/;
const GLOBAL_TOPIC_RE = /^global\/topics\/(.+)$/;
const PROJECT_TOPIC_RE = /^project\/([^/]+)\/topics\/(.+)$/;

/** Parse a synthetic leaf path back to (kind, scope[, slug]). Unknown → global 画像 (safe default). */
function parseLeaf(path: string): MemoryLeaf {
  if (path === GLOBAL_PREFERENCES_PATH)
    return { kind: "preferences", folderId: null };
  if (path === GLOBAL_PROFILE_PATH) return { kind: "profile", folderId: null };
  const proj = PROJECT_PROFILE_RE.exec(path);
  if (proj) return { kind: "profile", folderId: proj[1] };
  const nav = PROJECT_NAVIGATION_RE.exec(path);
  if (nav) return { kind: "navigation", folderId: nav[1] };
  const gt = GLOBAL_TOPIC_RE.exec(path);
  if (gt) return { kind: "topic", folderId: null, slug: gt[1] };
  const pt = PROJECT_TOPIC_RE.exec(path);
  if (pt) return { kind: "topic", folderId: pt[1], slug: pt[2] };
  return { kind: "profile", folderId: null };
}

/**
 * If `path` addresses a *project's* 画像 leaf, return its folderId, else null. Lets the
 * detail pane swap that one leaf for the two-pane 全局+本项目 editor while every other
 * memory leaf opens in the plain single-file editor.
 */
export function parseProjectProfilePath(path: string): string | null {
  const m = PROJECT_PROFILE_RE.exec(path);
  return m ? m[1] : null;
}

/**
 * folderId encoded in a project-scoped synthetic memory path (`project/<id>/profile`,
 * `project/<id>/navigation`, or `project/<id>/topics/<slug>`), else null. Used by 最近更新 /
 * 对话卡深链 to expand that project and its ``.agentcore`` node in the file rail.
 */
export function parseProjectMemoryFolderId(path: string): string | null {
  const profile = PROJECT_PROFILE_RE.exec(path);
  if (profile) return profile[1];
  const navigation = PROJECT_NAVIGATION_RE.exec(path);
  if (navigation) return navigation[1];
  const topic = PROJECT_TOPIC_RE.exec(path);
  return topic ? topic[1] : null;
}

/** True when `path` is an on-demand 主题 leaf (`…/topics/<slug>`), global or project. */
export function isMemoryTopicPath(path: string): boolean {
  return GLOBAL_TOPIC_RE.test(path) || PROJECT_TOPIC_RE.test(path);
}

/**
 * The display name (tab label) for a synthetic memory-leaf path — mirrors the rail's
 * naming so a deep-linked tab matches what the AgentCore entry rail would open: 偏好.md /
 * 画像.md / 导航.md / <slug>.md. A project 画像 opens the 双栏 editor which resolves the
 * project name from the live workspaces, so the bare「画像.md」is enough here.
 */
export function memoryLeafTabName(path: string): string {
  const leaf = parseLeaf(path);
  if (leaf.kind === "preferences") return "偏好.md";
  if (leaf.kind === "navigation") return "导航.md";
  if (leaf.kind === "topic") return `${leaf.slug}.md`;
  return "画像.md";
}

const unsupported = (): Promise<never> =>
  Promise.reject(new Error("记忆文档不支持该操作"));

/** Load one leaf's body + CAS version, dispatching topic notes to the topic surface. */
function loadLeaf(
  leaf: MemoryLeaf,
): Promise<{ content: string; version: string }> {
  return leaf.kind === "topic"
    ? getMemoryTopic(leaf.slug, leaf.folderId)
    : getMemoryFile(leaf.kind, leaf.folderId);
}

/** Write one leaf back (CAS-guarded), dispatching topic notes to the topic surface. */
function saveLeaf(
  leaf: MemoryLeaf,
  content: string,
  baseline: string | null,
): Promise<{ ok: boolean; version: string; conflict: boolean }> {
  return leaf.kind === "topic"
    ? writeMemoryTopic(leaf.slug, content, baseline, leaf.folderId)
    : writeMemoryFile(leaf.kind, content, baseline, leaf.folderId);
}

export function createMemorySource(): FileSource {
  return {
    id: "memory",
    label: "设定",
    caps: { watch: false, transfer: false, edit: true, snapshots: false },
    listDir: (): Promise<FileNode[]> => Promise.resolve([]),
    read: async (path): Promise<FilePreviewResult> => {
      const doc = await loadLeaf(parseLeaf(path));
      return { kind: "text", text: doc.content, truncated: false };
    },
    createFile: unsupported,
    mkdir: unsupported,
    move: unsupported,
    delete: unsupported,
    readForEdit: async (path) => {
      const doc = await loadLeaf(parseLeaf(path));
      return {
        text: doc.content,
        version: { etag: doc.version },
        encoding: "utf-8",
        eol: "lf",
      };
    },
    writeText: async (path, input) => {
      const r = await saveLeaf(
        parseLeaf(path),
        input.content,
        input.baseline?.etag ?? null,
      );
      return r.ok
        ? { ok: true as const, version: { etag: r.version } }
        : {
            ok: false as const,
            reason: "conflict" as const,
            version: { etag: r.version },
          };
    },
  };
}
