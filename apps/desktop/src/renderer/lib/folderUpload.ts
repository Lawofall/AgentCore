/**
 * 整夹上传：采集（拖入 or「选择文件夹」）+ 逐项落地。
 *
 * 两个入口产出同一种中间形态 {@link PickedUpload}，所以过滤、上限、报告只有一套实现：
 *
 * - **拖入**：`DataTransferItem.webkitGetAsEntry()` 拿到目录 entry 再递归展开。entry 必须在
 *   drop 事件里**同步**取走（{@link captureDropUpload}），事件一结束 `dataTransfer.items`
 *   就空了；展开可以异步慢慢做。
 * - **选择**：`<input type="file" webkitdirectory>`，相对路径在 `file.webkitRelativePath`。
 *
 * 落地刻意**不**沿用单文件上传的「串行 PUT、一失败即中断」：整夹一次上百个文件，中途
 * 断掉会留下说不清的半个目录。这里每一项独立成败、跑完全部，超限与失败**逐项**记进
 * {@link UploadReport}，由调用方原样展示——既不许一个 toast 吞掉整批，也不许静默跳过。
 */

import type { FileSource } from "@/lib/fileSource";
import { joinPath } from "@/lib/fileSource";

/**
 * 单文件硬顶，镜像服务端 ``workspace_upload_max_bytes``
 *（`apps/server/agentcore/config/workspace.py`）。超限项不发请求，直接记进报告。
 */
export const UPLOAD_MAX_BYTES = 50 * 1024 * 1024;

/** 单次整夹上传的文件数上限（对齐主进程 `ARCHIVE_MAX_FILES`）；命中即诚实报告截断。 */
export const UPLOAD_MAX_FILES = 20000;

/**
 * 上传时跳过的噪音目录——`main/fs/workspaceIgnore.ts` 同名集合的**渲染层手抄副本**
 *（渲染层不 import 主进程模块）。
 *
 * 由 `apps/server/scripts/check_workspace_ignore_parity.py` 与服务端 `IGNORED_DIRS`
 * 对账，漏改任一侧必红。
 */
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

/** 系统噪音后缀（同上，手抄副本 + 对账门禁）。AI 噪音后缀不参与——那是 AI 视角，用户自己传的图片/压缩包要留。 */
export const SYSTEM_IGNORED_FILE_SUFFIXES = [
  ".db",
  ".sqlite",
  ".sqlite3",
  ".pyc",
  ".pyo",
] as const;

/** 待上传的一个文件 + 它相对本次选择根的路径（`设计/图标/a.png`）。 */
export interface PickedFile {
  relPath: string;
  file: File;
}

/** 一次采集的结果（两个入口共用）。 */
export interface PickedUpload {
  files: PickedFile[];
  /** 遇到的目录（含空目录），按深度排好，先建再传。 */
  dirs: string[];
  /** 按忽略规则跳过的路径——报告里要说出来，不静默。 */
  ignored: string[];
  /** 命中 {@link UPLOAD_MAX_FILES} 而没采全。 */
  truncated: boolean;
}

/** 一项没成的上传。 */
export interface UploadFailure {
  path: string;
  reason: string;
}

/** 一次上传的完整交代。 */
export interface UploadReport {
  /** 目标目录（`""` = 源根）。 */
  destDir: string;
  uploaded: number;
  ignored: string[];
  failures: UploadFailure[];
  truncated: boolean;
}

const EMPTY_UPLOAD: PickedUpload = {
  files: [],
  dirs: [],
  ignored: [],
  truncated: false,
};

/** 归一成源内 POSIX 相对路径；空路径与含 `..` 的路径一律丢弃。 */
function normalizeUploadPath(raw: string): string {
  const p = raw.replace(/\\/g, "/").replace(/^\/+/, "");
  if (!p) return "";
  const parts = p.split("/").filter((s) => s && s !== ".");
  if (parts.some((s) => s === "..")) return "";
  return parts.join("/");
}

function hasIgnoredSuffix(name: string): boolean {
  const lower = name.toLowerCase();
  return SYSTEM_IGNORED_FILE_SUFFIXES.some((suffix) => lower.endsWith(suffix));
}

/** 路径上任一目录段是噪音目录，或文件名是系统噪音后缀。 */
export function isIgnoredUploadPath(relPath: string): boolean {
  const parts = relPath.split("/");
  const name = parts[parts.length - 1];
  if (parts.slice(0, -1).some((dir) => LIST_FILES_SKIP_DIRS.has(dir))) {
    return true;
  }
  return hasIgnoredSuffix(name);
}

function addAncestorDirs(dirs: Set<string>, dir: string): void {
  const parts = dir.split("/");
  for (let i = 1; i <= parts.length; i++) {
    dirs.add(parts.slice(0, i).join("/"));
  }
}

/** 深度浅的目录排前面，保证「先建父再建子」。 */
function byDepth(a: string, b: string): number {
  const depth = a.split("/").length - b.split("/").length;
  return depth !== 0 ? depth : a.localeCompare(b);
}

/** 从 `<input webkitdirectory>` / `<input multiple>` 的 FileList 采集。 */
export function collectPickedFiles(list: FileList | null): PickedUpload {
  if (!list || list.length === 0) return EMPTY_UPLOAD;
  const files: PickedFile[] = [];
  const ignored: string[] = [];
  const dirs = new Set<string>();
  let truncated = false;

  for (const file of Array.from(list)) {
    // webkitRelativePath 只有目录选择才有；普通多选退回裸文件名。
    const relPath = normalizeUploadPath(file.webkitRelativePath || file.name);
    if (!relPath) continue;
    if (isIgnoredUploadPath(relPath)) {
      ignored.push(relPath);
      continue;
    }
    if (files.length >= UPLOAD_MAX_FILES) {
      truncated = true;
      break;
    }
    files.push({ relPath, file });
    const cut = relPath.lastIndexOf("/");
    if (cut > 0) addAncestorDirs(dirs, relPath.slice(0, cut));
  }
  return { files, dirs: [...dirs].sort(byDepth), ignored, truncated };
}

/**
 * drop 事件里**同步**取走的东西：目录/文件 entry（可异步展开）+ 拿不到 entry 时的裸文件。
 *
 * `dataTransfer.items` 在事件回调返回后即失效，所以这一步不能 await 任何东西。
 */
export interface DropUploadCapture {
  entries: FileSystemEntry[];
  looseFiles: File[];
}

export function captureDropUpload(dt: DataTransfer): DropUploadCapture {
  const entries: FileSystemEntry[] = [];
  const looseFiles: File[] = [];
  for (const item of Array.from(dt.items ?? [])) {
    if (item.kind !== "file") continue;
    const entry = item.webkitGetAsEntry?.() ?? null;
    if (entry) {
      entries.push(entry);
      continue;
    }
    const file = item.getAsFile();
    if (file) looseFiles.push(file);
  }
  // 没有 items（老宿主 / 合成事件）时退回 files——文件夹在这条路上本就取不到。
  if (entries.length === 0 && looseFiles.length === 0) {
    looseFiles.push(...Array.from(dt.files ?? []));
  }
  return { entries, looseFiles };
}

/** 这次拖拽里有目录吗（决定提示语说「上传」还是「上传文件夹」）。 */
export function captureHasDirectory(capture: DropUploadCapture): boolean {
  return capture.entries.some((e) => e.isDirectory);
}

function readFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => {
    entry.file(resolve, reject);
  });
}

/** `readEntries` 一次最多给 100 项，要一直读到空批为止。 */
async function readAllEntries(
  reader: FileSystemDirectoryReader,
): Promise<FileSystemEntry[]> {
  const all: FileSystemEntry[] = [];
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => {
      reader.readEntries(resolve, reject);
    });
    if (batch.length === 0) return all;
    all.push(...batch);
  }
}

/** 把同步取走的 entry 递归展开成 {@link PickedUpload}。 */
export async function expandDropUpload(
  capture: DropUploadCapture,
): Promise<PickedUpload> {
  const files: PickedFile[] = [];
  const ignored: string[] = [];
  const dirs = new Set<string>();
  let truncated = false;

  for (const file of capture.looseFiles) {
    const relPath = normalizeUploadPath(file.name);
    if (!relPath) continue;
    if (isIgnoredUploadPath(relPath)) {
      ignored.push(relPath);
      continue;
    }
    if (files.length >= UPLOAD_MAX_FILES) {
      truncated = true;
      break;
    }
    files.push({ relPath, file });
  }

  const walk = async (
    entry: FileSystemEntry,
    parent: string,
  ): Promise<void> => {
    if (truncated) return;
    const relPath = normalizeUploadPath(joinPath(parent, entry.name));
    if (!relPath) return;
    if (entry.isDirectory) {
      if (LIST_FILES_SKIP_DIRS.has(entry.name)) {
        ignored.push(relPath);
        return;
      }
      dirs.add(relPath);
      const children = await readAllEntries(
        (entry as FileSystemDirectoryEntry).createReader(),
      );
      for (const child of children) await walk(child, relPath);
      return;
    }
    if (hasIgnoredSuffix(entry.name)) {
      ignored.push(relPath);
      return;
    }
    if (files.length >= UPLOAD_MAX_FILES) {
      truncated = true;
      return;
    }
    files.push({ relPath, file: await readFile(entry as FileSystemFileEntry) });
  };

  for (const entry of capture.entries) await walk(entry, "");
  return { files, dirs: [...dirs].sort(byDepth), ignored, truncated };
}

/**
 * 一次上传的 toast 文案。`hasDetail` 为真时调用方必须给出「查看详情」入口——
 * 概述里的数字不能替代逐项清单。
 */
export function describeUploadReport(report: UploadReport): {
  message: string;
  description?: string;
  hasDetail: boolean;
} {
  const bits: string[] = [];
  if (report.failures.length > 0) {
    bits.push(`${report.failures.length} 项未上传`);
  }
  if (report.ignored.length > 0) {
    bits.push(`跳过 ${report.ignored.length} 个忽略项`);
  }
  if (report.truncated) {
    bits.push(`超过 ${UPLOAD_MAX_FILES} 个文件，只取了前一批`);
  }
  const message =
    report.uploaded > 0
      ? `已上传 ${report.uploaded} 个文件`
      : report.failures.length > 0
        ? "没有文件上传成功"
        : "没有可上传的文件";
  return {
    message,
    description: bits.length > 0 ? `${bits.join("；")}。` : undefined,
    hasDetail: bits.length > 0,
  };
}

/**
 * 逐项上传，**不因单项失败中断**。目录先建（保住空目录，也免得每次 PUT 都隐式建父），
 * 建目录失败不单独记——真出问题会在它下面的文件上如实报出来。
 */
export async function uploadPicked(
  picked: PickedUpload,
  destDir: string,
  source: Pick<FileSource, "mkdir" | "writeBytes">,
  onProgress?: (done: number, total: number) => void,
): Promise<UploadReport> {
  const writeBytes = source.writeBytes;
  const failures: UploadFailure[] = [];
  let uploaded = 0;

  if (!writeBytes) {
    return {
      destDir,
      uploaded: 0,
      ignored: picked.ignored,
      truncated: picked.truncated,
      failures: picked.files.map((f) => ({
        path: f.relPath,
        reason: "此工作区不支持上传",
      })),
    };
  }

  for (const dir of picked.dirs) {
    try {
      await source.mkdir(joinPath(destDir, dir));
    } catch {
      // 已存在是常态；真不可写会在其下的文件上如实报出来。
    }
  }

  const total = picked.files.length;
  for (let i = 0; i < total; i++) {
    const item = picked.files[i];
    onProgress?.(i, total);
    if (item.file.size > UPLOAD_MAX_BYTES) {
      failures.push({
        path: item.relPath,
        reason: `超过单文件 ${Math.round(UPLOAD_MAX_BYTES / (1024 * 1024))}MB 上限`,
      });
      continue;
    }
    try {
      await writeBytes(joinPath(destDir, item.relPath), item.file);
      uploaded += 1;
    } catch (e) {
      failures.push({
        path: item.relPath,
        reason: e instanceof Error && e.message ? e.message : "上传失败",
      });
    }
  }
  onProgress?.(total, total);

  return {
    destDir,
    uploaded,
    ignored: picked.ignored,
    failures,
    truncated: picked.truncated,
  };
}
