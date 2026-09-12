/**
 * 工作区列举忽略规则（两档，与服务端 `workspace/_paths.py` 对齐）。
 *
 * 改名单须双边同步；对账门禁（漏改一侧必红）::
 *
 *   cd apps/server && uv run python scripts/check_workspace_ignore_parity.py
 *
 * - **系统噪音**：对 AI 与用户文件 UI 都隐藏（目录 + `*.db` / `*.pyc` 等）。
 * - **AI 噪音**：媒体 / 压缩包 / 字体 / 二进制对象——`collectWorkspaceFiles` /
 *   `opIndexFiles` / grep 仍排除。`opList` / `opListTree` 按名列出图片；压缩包
 *   在 `attachments/`、区外 `external/`、`revealArchives` 或本回合 `reveal_paths`
 *   下豁免（与服务端 `file_list` / `list_tree` 对齐）。文件 UI（`listDir`）只藏系统噪音。
 *
 * 同树旁路 `AgentCore/{index,trash,baselines,versions}` 为路径感知系统噪音（禁止把裸名
 * `index`/`trash`/`baselines`/`versions` 放进全局跳过集，以免误伤用户项目）。
 * 新增区名要同时改三处（服务端 `stage_dirs.py`、本文件、渲染层
 * `services/sources/workspaceSource.ts` 内联副本）——同一门禁对账区名集与
 * `AgentCore/<zone>` 路径形态，漏改任一处必红。
 */

/** 与服务端 `stage_dirs.AGENTCORE_ROOT` 对齐。 */
export const AGENTCORE_ROOT = "AgentCore";

/** 与服务端 `attachments.ATTACHMENTS_DIR` / `is_attachment_path` 对齐。 */
export const ATTACHMENTS_DIR = "attachments";

/** 与服务端 `stage_dirs.INTERNAL_ZONE_NAMES` 对齐。 */
export const INTERNAL_ZONE_NAMES = new Set([
  "index",
  "trash",
  "baselines",
  "versions",
]);

export const INDEX_REL = `${AGENTCORE_ROOT}/index`;
export const TRASH_REL = `${AGENTCORE_ROOT}/trash`;
export const BASELINES_REL = `${AGENTCORE_ROOT}/baselines`;
/** 用户命名版本区（`versions/<id>/{meta.json,content.zip}`）——本地版「留版本」。 */
export const VERSIONS_REL = `${AGENTCORE_ROOT}/versions`;

/** 系统噪音目录（整棵子树）。↔ 服务端 `IGNORED_DIRS`（parity gate）。 */
export const LIST_FILES_SKIP_DIRS = new Set([
  ".git",
  ".hg",
  ".svn",
  "node_modules",
  "bower_components",
  "vendor",
  "__pycache__",
  ".venv",
  "venv",
  ".tox",
  ".nox",
  ".eggs",
  ".mypy_cache",
  ".pytest_cache",
  ".pytest_tmp",
  ".ruff_cache",
  ".turbo",
  ".cache",
  ".parcel-cache",
  ".pnpm-store",
  "coverage",
  "htmlcov",
  ".idea",
  ".vscode",
  "dist",
  "build",
  ".next",
  ".nuxt",
  ".vite",
  ".svelte-kit",
  ".wrangler",
  "out",
  "target",
  "logs",
  "tmp",
  "temp",
  ".tmp",
]);

/** 系统噪音后缀（UI + AI）。↔ 服务端 `SYSTEM_IGNORED_FILE_SUFFIXES`（parity gate）。 */
export const SYSTEM_IGNORED_FILE_SUFFIXES = [
  ".db",
  ".sqlite",
  ".sqlite3",
  ".pyc",
  ".pyo",
] as const;

/** AI 噪音后缀（仅 AI）。↔ 服务端 `AI_NOISE_FILE_SUFFIXES`（parity gate）。 */
export const AI_NOISE_FILE_SUFFIXES = [
  ".class",
  ".o",
  ".a",
  ".lib",
  ".so",
  ".dylib",
  ".dll",
  ".exe",
  ".wasm",
  ".bin",
  ".dat",
  ".pack",
  ".idx",
  // Runtime log files (``logs/`` dirs are system-noise; loose ``*.log`` is AI-only).
  ".log",
  // Columnar / numeric / serialized data blobs (not source text).
  ".parquet",
  ".feather",
  ".arrow",
  ".npy",
  ".h5",
  ".hdf5",
  ".pkl",
  ".pickle",
  // Images: grep/index still skip; opList/glob show (AI_IMAGE subset).
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".ico",
  ".bmp",
  ".mp3",
  ".mp4",
  ".wav",
  ".webm",
  ".zip",
  ".tar",
  ".gz",
  ".tgz",
  ".bz2",
  ".7z",
  ".rar",
  ".woff",
  ".woff2",
  ".ttf",
  ".otf",
  ".eot",
] as const;

/** 图片后缀（AI 噪音子集；列表可见）。↔ 服务端 `AI_IMAGE_FILE_SUFFIXES`（parity gate）。 */
export const AI_IMAGE_FILE_SUFFIXES = [
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".ico",
  ".bmp",
] as const;

/** 压缩包后缀（AI 噪音子集）。↔ 服务端 `AI_ARCHIVE_FILE_SUFFIXES`（parity gate）。 */
export const AI_ARCHIVE_FILE_SUFFIXES = [
  ".zip",
  ".tar",
  ".gz",
  ".tgz",
  ".bz2",
  ".7z",
  ".rar",
] as const;

function endsWithAny(name: string, suffixes: readonly string[]): boolean {
  const lower = name.toLowerCase();
  return suffixes.some((suf) => lower.endsWith(suf));
}

/** True when relPath is `AgentCore/{index|trash|baselines|versions}` or under it. */
export function isInternalZoneRelPath(relPath: string): boolean {
  const p = relPath.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!p || p === ".") return false;
  for (const zone of INTERNAL_ZONE_NAMES) {
    const prefix = `${AGENTCORE_ROOT}/${zone}`;
    if (p === prefix || p.startsWith(`${prefix}/`)) return true;
  }
  return false;
}

/** Whether ``path`` lives under the resident ``attachments/`` directory. */
export function isAttachmentPath(path: string): boolean {
  const p = path
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "")
    .replace(/^\/+|\/+$/g, "");
  return p === ATTACHMENTS_DIR || p.startsWith(`${ATTACHMENTS_DIR}/`);
}

/**
 * 是否跳过目录名。`parentRel` 为父目录的工作区相对路径（根用 `""`），
 * 用于路径感知内部区；裸名 `index` 等不单独跳过。
 */
export function shouldSkipDirName(name: string, parentRel = ""): boolean {
  if (LIST_FILES_SKIP_DIRS.has(name)) return true;
  const parent = parentRel.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  const child = parent && parent !== "." ? `${parent}/${name}` : name;
  return isInternalZoneRelPath(child);
}

/** 系统噪音文件（UI + AI 都隐藏）。 */
export function shouldSkipSystemFileName(name: string): boolean {
  return endsWithAny(name, SYSTEM_IGNORED_FILE_SUFFIXES);
}

/** AI 噪音文件（仅 AI 隐藏；用户 UI 可见）。 */
export function shouldSkipAiNoiseFileName(name: string): boolean {
  return endsWithAny(name, AI_NOISE_FILE_SUFFIXES);
}

/** AI 视角：系统噪音 ∪ AI 噪音。 */
export function shouldSkipFileName(name: string): boolean {
  return shouldSkipSystemFileName(name) || shouldSkipAiNoiseFileName(name);
}

/** AI 列举 / 索引 / grep：目录系统噪音 + 文件全档（无 attachments 豁免）。 */
export function shouldSkipWorkspaceEntry(
  name: string,
  isDirectory: boolean,
  parentRel = "",
): boolean {
  return isDirectory
    ? shouldSkipDirName(name, parentRel)
    : shouldSkipFileName(name);
}

/** Options for AI list hide exemptions (parity with server sparse_listing). */
export type AiListSkipOptions = {
  /** ``file_list`` pattern 指向压缩包后缀时为 true。 */
  revealArchives?: boolean;
  /** 当前根为会话区外 mount（desktop session root）时为 true。 */
  externalNs?: boolean;
};

/** Whether ``path`` is under the model-facing ``external/<alias>/`` namespace. */
export function isExternalNsPath(path: string): boolean {
  const p = path
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "")
    .replace(/^\/+|\/+$/g, "");
  return p === "external" || p.startsWith("external/");
}

/** AI-noise image basename (png/webp/…). Listed by name; grep still skips. */
export function isAiImageFileName(name: string): boolean {
  return endsWithAny(name, AI_IMAGE_FILE_SUFFIXES);
}

/** AI-noise archive basename (zip/rar/7z/…). */
export function shouldSkipAiArchiveFileName(name: string): boolean {
  return endsWithAny(name, AI_ARCHIVE_FILE_SUFFIXES);
}

/**
 * AI ``opList`` / ``opListTree``：系统噪音始终隐藏；图片按名列出；其它 AI 噪音
 * 在 ``attachments/`` 或 ``revealPaths`` 下豁免；压缩包在区外 ``external/`` /
 * session mount / ``revealArchives`` 时豁免（与服务端 ``is_ai_list_hidden_file``
 * 对齐）。索引 / grep 仍用 {@link shouldSkipWorkspaceEntry}。
 */
export function shouldSkipAiListEntry(
  name: string,
  isDirectory: boolean,
  parentRel = "",
  revealPaths?: ReadonlySet<string>,
  options?: AiListSkipOptions,
): boolean {
  if (isDirectory) return shouldSkipDirName(name, parentRel);
  if (shouldSkipSystemFileName(name)) return true;
  if (!shouldSkipAiNoiseFileName(name)) return false;
  if (isAiImageFileName(name)) return false;
  const parent = parentRel.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  const child = parent && parent !== "." ? `${parent}/${name}` : name;
  if (isAttachmentPath(child)) return false;
  if (revealPaths?.has(child)) return false;
  if (shouldSkipAiArchiveFileName(name)) {
    if (options?.revealArchives) return false;
    if (options?.externalNs || isExternalNsPath(child)) return false;
  }
  return true;
}

/** 用户文件 UI（`listDir`）：仅系统噪音。 */
export function shouldSkipSystemWorkspaceEntry(
  name: string,
  isDirectory: boolean,
  parentRel = "",
): boolean {
  return isDirectory
    ? shouldSkipDirName(name, parentRel)
    : shouldSkipSystemFileName(name);
}
