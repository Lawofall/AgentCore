/**
 * 附加文件：区内引用原路径；区外才复制进对话工作区 ``attachments/``。
 *
 * 绝对路径只在主进程出现；renderer 只拿到 ``name`` / ``workspacePath`` / 可选文本预览 /
 * ``citedRootId``+``citedRelPath``。云占位（OneDrive 按需下载等）**不做前置检测**：
 * 读/复制各带短超时，失败之后才回头诊断，免得每附加一个文件都先为一次 powershell
 * 探测付秒级延迟。
 */

import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { promises as fs, createReadStream, createWriteStream } from "node:fs";
import { basename, extname, isAbsolute, join, relative } from "node:path";
import { pipeline } from "node:stream/promises";
import { promisify } from "node:util";
import type { FsErrorCode, FsResult } from "@shared/ipc-contract";
import { BrowserWindow, app, dialog } from "electron";
import { IMAGE_MIME, TEXT_PREVIEW_CAP } from "./constants";
import { locate, realInside } from "./pathGuard";
import { sniffBinary } from "./preview";
import { ensureReady, getAllRoots, getRoot } from "./roots";

const execFileAsync = promisify(execFile);

/** 与服务端 ``workspace_upload_max_bytes`` 对齐。 */
export const ATTACH_MAX_BYTES = 50 * 1024 * 1024;
/** 占位文件 / 网络盘 open 挂起时快速失败（勿吃满 code_execute 的 30–60s）。 */
export const ATTACH_COPY_TIMEOUT_MS = 8_000;

const ATTACHMENTS_DIR = "attachments";
const UNSYNCED_HINT = "文件可能未同步到本地，请在资源管理器中打开一次后再附加";

export interface StageDest {
  rootId: string;
  /** 工作区在授权根下的子路径（scratch / 项目 subpath）；空 = 根自身。 */
  subpath?: string;
}

export interface StagedAttachmentData {
  name: string;
  /** 已在对话工作区时的相对路径（区内原路径，或 ``attachments/<name>``）。 */
  workspacePath?: string;
  /** 尚未落工作区时的暂存 id（草稿 / 云端待上传）。 */
  stagingId?: string;
  binary: boolean;
  /** UTF-8 文本预览（二进制为空）；供 prompt 内联。 */
  text: string;
  truncated: boolean;
  sizeBytes: number;
  citedRootId?: string;
  citedRelPath?: string;
}

interface StagingEntry {
  absPath: string;
  name: string;
  binary: boolean;
  text: string;
  truncated: boolean;
  sizeBytes: number;
}

const staging = new Map<string, StagingEntry>();
/** Disk scan once so ``stagingId`` survives app restart (files under attach-staging/). */
let stagingHydrated = false;
/** Module load ≈ app start — anything staged after it belongs to the live session. */
const APP_START_MS = Date.now();

function stagingDir(): string {
  return join(app.getPath("userData"), "attach-staging");
}

/**
 * Rebuild in-memory staging index from ``attach-staging/<id>/<name>`` left on disk
 * after a previous session. Idempotent; call before consume/finalize.
 */
export async function hydrateStagingFromDisk(): Promise<void> {
  if (stagingHydrated) return;
  stagingHydrated = true;
  let ids: string[];
  try {
    ids = await fs.readdir(stagingDir());
  } catch {
    return;
  }
  for (const id of ids) {
    if (staging.has(id)) continue;
    const idDir = join(stagingDir(), id);
    let st: Awaited<ReturnType<typeof fs.stat>>;
    try {
      st = await fs.stat(idDir);
    } catch {
      continue;
    }
    if (!st.isDirectory()) continue;
    let files: string[];
    try {
      files = await fs.readdir(idDir);
    } catch {
      continue;
    }
    const fileName = files.find((f) => f && !f.startsWith("."));
    if (!fileName) {
      try {
        await fs.rm(idDir, { recursive: true, force: true });
      } catch {
        /* ignore */
      }
      continue;
    }
    const absPath = join(idDir, fileName);
    const mat = await materializeSource(absPath, "staged");
    if (!mat.ok) {
      try {
        await fs.rm(idDir, { recursive: true, force: true });
      } catch {
        /* ignore */
      }
      continue;
    }
    staging.set(id, mat.data);
  }
}

/**
 * Delete ``attach-staging/<id>`` dirs that no live draft references.
 *
 * Draft attachment metadata lives in renderer localStorage, capped to the most
 * recent drafts — once a draft is evicted (or its conversation cleared) nothing
 * points at its staged bytes again, so without this they accumulate forever.
 *
 * Only dirs predating this launch are eligible, which makes the sweep safe to
 * run against a live session: an attachment staged right now cannot be reaped
 * no matter what the caller's snapshot of ids says.
 */
export async function sweepStagingOrphans(
  liveStagingIds: string[],
): Promise<void> {
  const live = new Set(liveStagingIds);
  let ids: string[];
  try {
    ids = await fs.readdir(stagingDir());
  } catch {
    return;
  }
  for (const id of ids) {
    if (live.has(id)) continue;
    try {
      const idDir = join(stagingDir(), id);
      const st = await fs.stat(idDir);
      if (!st.isDirectory() || st.mtimeMs >= APP_START_MS) continue;
      await fs.rm(idDir, { recursive: true, force: true });
      staging.delete(id);
    } catch {
      /* leave it for the next sweep */
    }
  }
}

/** @internal vitest — forget in-memory index so the next lookup rescans disk. */
export function __resetStagingMemoryForTests(): void {
  staging.clear();
  stagingHydrated = false;
}

async function lookupStaging(
  stagingId: string,
): Promise<StagingEntry | undefined> {
  await hydrateStagingFromDisk();
  return staging.get(stagingId);
}

function safeName(name: string): string {
  const base = basename((name || "").replace(/\\/g, "/").trim()).replace(
    /^\.+/,
    "",
  );
  return base || "attachment";
}

function dedupName(name: string, used: Set<string>): string {
  if (!used.has(name)) {
    used.add(name);
    return name;
  }
  const root = name.includes(".") ? name.slice(0, name.lastIndexOf(".")) : name;
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".")) : "";
  let i = 2;
  let candidate = `${root} (${i})${ext}`;
  while (used.has(candidate)) {
    i += 1;
    candidate = `${root} (${i})${ext}`;
  }
  used.add(candidate);
  return candidate;
}

async function listExistingAttachmentNames(
  destRootId: string,
  destSubpath: string,
): Promise<Set<string>> {
  const used = new Set<string>();
  const root = getRoot(destRootId);
  if (!root) return used;
  const rel = destSubpath
    ? `${destSubpath.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "")}/${ATTACHMENTS_DIR}`
    : ATTACHMENTS_DIR;
  const loc = locate(destRootId, rel);
  if ("error" in loc) return used;
  try {
    const entries = await fs.readdir(loc.abs);
    for (const e of entries) used.add(e);
  } catch {
    /* dir missing — empty */
  }
  return used;
}

/**
 * Windows 云占位检测：Offline / RecallOnDataAccess / RecallOnOpen。
 * 非 Windows 返回 false（仍靠复制超时兜底）。
 *
 * 每次调用要起一个 powershell.exe（冷启动 300ms–2s，上限 2s），所以只允许出现在
 * 失败分支上——见 ``diagnoseCloudFailure``。
 */
export async function isCloudPlaceholder(absPath: string): Promise<boolean> {
  if (process.platform !== "win32") return false;
  const escaped = absPath.replace(/'/g, "''");
  try {
    const { stdout } = await execFileAsync(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        `(Get-Item -LiteralPath '${escaped}').Attributes.ToString()`,
      ],
      { timeout: 2_000, windowsHide: true },
    );
    const attrs = stdout.toLowerCase();
    return (
      attrs.includes("offline") ||
      attrs.includes("recallondataaccess") ||
      attrs.includes("recallonopen")
    );
  } catch {
    return false;
  }
}

/**
 * 待读字节的来源：``user`` = 用户挑的原始路径（可能是 OneDrive 云占位）；
 * ``staged`` = ``attach-staging/`` 下本进程写的副本，不可能是占位。
 */
type ByteSource = "user" | "staged";

type FsFailure = { ok: false; reason: string; code: FsErrorCode };

/**
 * 读/复制失败后追问一句「是不是云占位」，把泛化 IO 错误升级成可操作的未同步提示。
 *
 * 只在失败分支调用：正常附件一次 powershell 都不起；超时分支本就报未同步提示，
 * 无需再探（那条路径非 Windows 也一直靠它兜底）。
 */
async function diagnoseCloudFailure(
  absPath: string,
  source: ByteSource,
  fallback: FsFailure,
): Promise<FsFailure> {
  if (source === "staged") return fallback;
  if (await isCloudPlaceholder(absPath)) {
    return { ok: false, reason: UNSYNCED_HINT, code: "busy" };
  }
  return fallback;
}

async function withTimeout<T>(
  p: Promise<T>,
  ms: number,
  label: string,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      p,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label}_TIMEOUT`)), ms);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function copyFileTimed(src: string, dest: string): Promise<void> {
  await fs.mkdir(dirnameSafe(dest), { recursive: true });
  await withTimeout(
    pipeline(createReadStream(src), createWriteStream(dest)),
    ATTACH_COPY_TIMEOUT_MS,
    "COPY",
  );
}

function dirnameSafe(p: string): string {
  const i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return i >= 0 ? p.slice(0, i) : ".";
}

async function resolveDestAbs(
  dest: StageDest,
  fileName: string,
): Promise<FsResult<string>> {
  await ensureReady();
  const root = getRoot(dest.rootId);
  if (!root) {
    return {
      ok: false,
      reason: "本地目录未授权或已移除",
      code: "unauthorized",
    };
  }
  const sub = (dest.subpath || "")
    .replace(/\\/g, "/")
    .replace(/^\/+|\/+$/g, "");
  const rel = sub
    ? `${sub}/${ATTACHMENTS_DIR}/${fileName}`
    : `${ATTACHMENTS_DIR}/${fileName}`;
  const loc = locate(dest.rootId, rel);
  if ("error" in loc) return loc.error;
  // 目标可能尚不存在——用词法路径 + 父目录 realInside 校验。
  const parentRel = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "";
  if (parentRel) {
    const parentLoc = locate(dest.rootId, parentRel);
    if ("error" in parentLoc) return parentLoc.error;
    try {
      await fs.mkdir(parentLoc.abs, { recursive: true });
    } catch (e) {
      return {
        ok: false,
        reason: e instanceof Error ? e.message : "无法创建 attachments 目录",
        code: "error",
      };
    }
    const parentReal = await realInside(root, parentLoc.abs);
    if (!parentReal.ok) {
      return {
        ok: false,
        reason: parentReal.reason,
        code: parentReal.code,
      };
    }
  }
  return { ok: true, data: loc.abs };
}

async function materializeSource(
  absPath: string,
  source: ByteSource,
): Promise<FsResult<Omit<StagingEntry, "absPath"> & { absPath: string }>> {
  let st: Awaited<ReturnType<typeof fs.stat>>;
  try {
    st = await withTimeout(fs.stat(absPath), 2_000, "STAT");
  } catch (e) {
    const msg = e instanceof Error ? e.message : "";
    if (msg.includes("TIMEOUT")) {
      return { ok: false, reason: UNSYNCED_HINT, code: "busy" };
    }
    return { ok: false, reason: "文件不存在或无法访问", code: "not_found" };
  }
  if (!st.isFile()) {
    return { ok: false, reason: "只能附加普通文件", code: "invalid" };
  }
  if (st.size > ATTACH_MAX_BYTES) {
    return {
      ok: false,
      reason: `文件超过 ${Math.round(ATTACH_MAX_BYTES / (1024 * 1024))}MB 上限`,
      code: "invalid",
    };
  }

  const ext = extname(absPath).toLowerCase();
  const name = safeName(basename(absPath));
  // 图片按二进制驻留（不内联 UTF-8）；识图能力由后端/模型配置决定，前端不硬拒。
  if (IMAGE_MIME[ext]) {
    return {
      ok: true,
      data: {
        absPath,
        name,
        binary: true,
        text: "",
        truncated: false,
        sizeBytes: st.size,
      },
    };
  }

  // 读入内存做二进制嗅探 + 文本预览；整文件仍经流式复制落盘（见 copyFileTimed）。
  let head: Buffer;
  try {
    const fh = await withTimeout(fs.open(absPath, "r"), 2_000, "OPEN");
    try {
      const buf = Buffer.alloc(Math.min(st.size, TEXT_PREVIEW_CAP + 1));
      const { bytesRead } = await withTimeout(
        fh.read(buf, 0, buf.length, 0),
        ATTACH_COPY_TIMEOUT_MS,
        "READ",
      );
      head = buf.subarray(0, bytesRead);
    } finally {
      await fh.close();
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : "";
    if (msg.includes("TIMEOUT")) {
      return { ok: false, reason: UNSYNCED_HINT, code: "busy" };
    }
    return diagnoseCloudFailure(absPath, source, {
      ok: false,
      reason: "读取文件失败",
      code: "error",
    });
  }

  const binary = sniffBinary(head);
  if (binary) {
    return {
      ok: true,
      data: {
        absPath,
        name,
        binary: true,
        text: "",
        truncated: false,
        sizeBytes: st.size,
      },
    };
  }

  const truncated = st.size > TEXT_PREVIEW_CAP;
  const text = head
    .subarray(0, Math.min(head.length, TEXT_PREVIEW_CAP))
    .toString("utf-8");
  return {
    ok: true,
    data: {
      absPath,
      name,
      binary: false,
      text,
      truncated,
      sizeBytes: st.size,
    },
  };
}

async function writeToDest(
  entry: StagingEntry,
  dest: StageDest,
  source: ByteSource,
): Promise<FsResult<StagedAttachmentData>> {
  const used = await listExistingAttachmentNames(
    dest.rootId,
    dest.subpath || "",
  );
  const fileName = dedupName(entry.name, used);
  const destRes = await resolveDestAbs(dest, fileName);
  if (!destRes.ok) return destRes;

  try {
    await copyFileTimed(entry.absPath, destRes.data);
  } catch (e) {
    const msg = e instanceof Error ? e.message : "";
    if (msg.includes("TIMEOUT")) {
      return { ok: false, reason: UNSYNCED_HINT, code: "busy" };
    }
    return diagnoseCloudFailure(entry.absPath, source, {
      ok: false,
      reason: "复制到工作区失败",
      code: "error",
    });
  }

  return {
    ok: true,
    data: {
      name: fileName,
      workspacePath: `${ATTACHMENTS_DIR}/${fileName}`,
      binary: entry.binary,
      text: entry.text,
      truncated: entry.truncated,
      sizeBytes: entry.sizeBytes,
    },
  };
}

async function stageToTemp(
  entry: StagingEntry,
): Promise<FsResult<StagedAttachmentData>> {
  const id = randomUUID();
  const dir = join(stagingDir(), id);
  await fs.mkdir(dir, { recursive: true });
  const stagedAbs = join(dir, entry.name);
  try {
    await copyFileTimed(entry.absPath, stagedAbs);
  } catch (e) {
    const msg = e instanceof Error ? e.message : "";
    try {
      await fs.rm(dir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
    if (msg.includes("TIMEOUT")) {
      return { ok: false, reason: UNSYNCED_HINT, code: "busy" };
    }
    // 暂存的源恒是用户挑的原始路径（只有 stageFromAbs 走到这），值得回头诊断一次。
    return diagnoseCloudFailure(entry.absPath, "user", {
      ok: false,
      reason: "暂存附件失败",
      code: "error",
    });
  }

  staging.set(id, {
    ...entry,
    absPath: stagedAbs,
  });

  return {
    ok: true,
    data: {
      name: entry.name,
      stagingId: id,
      binary: entry.binary,
      text: entry.text,
      truncated: entry.truncated,
      sizeBytes: entry.sizeBytes,
    },
  };
}

function toPosixRel(fromAbs: string, fileAbs: string): string | null {
  const rel = relative(fromAbs, fileAbs);
  if (!rel || rel.startsWith("..") || isAbsolute(rel)) return null;
  return rel.replace(/\\/g, "/");
}

async function realPathOrNull(abs: string): Promise<string | null> {
  try {
    return await fs.realpath(abs);
  } catch {
    return null;
  }
}

/** 文件已在 dest 工作区树内 → 引用原路径，不复制。 */
async function citeIfInsideDest(
  fileAbs: string,
  dest: StageDest,
  entry: StagingEntry,
): Promise<FsResult<StagedAttachmentData> | null> {
  await ensureReady();
  const root = getRoot(dest.rootId);
  if (!root) return null;
  const sub = (dest.subpath || "")
    .replace(/\\/g, "/")
    .replace(/^\/+|\/+$/g, "");
  const destAbs = sub ? join(root.absPath, ...sub.split("/")) : root.absPath;
  const destReal = await realPathOrNull(destAbs);
  const fileReal = await realPathOrNull(fileAbs);
  if (!destReal || !fileReal) return null;
  const posix = toPosixRel(destReal, fileReal);
  if (!posix) return null;
  const citedRel = sub ? `${sub}/${posix}` : posix;
  return {
    ok: true,
    data: {
      name: entry.name,
      workspacePath: posix,
      binary: entry.binary,
      text: entry.text,
      truncated: entry.truncated,
      sizeBytes: entry.sizeBytes,
      citedRootId: dest.rootId,
      citedRelPath: citedRel,
    },
  };
}

async function findContainingRoot(
  fileAbs: string,
): Promise<{ rootId: string; relPath: string } | null> {
  await ensureReady();
  const fileReal = await realPathOrNull(fileAbs);
  if (!fileReal) return null;
  const ranked = [...getAllRoots()].sort(
    (a, b) => b.absPath.length - a.absPath.length,
  );
  for (const root of ranked) {
    const rootReal = await realPathOrNull(root.absPath);
    if (!rootReal) continue;
    const posix = toPosixRel(rootReal, fileReal);
    if (posix) return { rootId: root.id, relPath: posix };
  }
  return null;
}

async function annotateDraftCite(
  staged: FsResult<StagedAttachmentData>,
  fileAbs: string,
): Promise<FsResult<StagedAttachmentData>> {
  if (!staged.ok) return staged;
  const found = await findContainingRoot(fileAbs);
  if (!found) return staged;
  return {
    ok: true,
    data: {
      ...staged.data,
      citedRootId: found.rootId,
      citedRelPath: found.relPath,
    },
  };
}

async function stageFromAbs(
  absPath: string,
  dest?: StageDest,
): Promise<FsResult<StagedAttachmentData>> {
  let resolved = absPath;
  try {
    resolved = await fs.realpath(absPath);
  } catch {
    /* keep lexical */
  }
  const mat = await materializeSource(resolved, "user");
  if (!mat.ok) return mat;
  if (dest) {
    const cited = await citeIfInsideDest(resolved, dest, mat.data);
    if (cited) return cited;
    return writeToDest(mat.data, dest, "user");
  }
  return annotateDraftCite(await stageToTemp(mat.data), resolved);
}

/** 系统文件选择器 → 驻留（有 dest）或暂存。 */
export async function pickAndStageAttachment(
  dest?: StageDest,
): Promise<FsResult<StagedAttachmentData> | null> {
  const win =
    BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0];
  const result = win
    ? await dialog.showOpenDialog(win, {
        properties: ["openFile"],
        title: "附加文件到对话",
      })
    : await dialog.showOpenDialog({
        properties: ["openFile"],
        title: "附加文件到对话",
      });
  if (result.canceled || result.filePaths.length === 0) return null;
  return stageFromAbs(result.filePaths[0], dest);
}

/** 从已授权根内相对路径驻留（@ 菜单）。 */
export async function stageFromRoot(
  rootId: string,
  relPath: string,
  dest?: StageDest,
): Promise<FsResult<StagedAttachmentData>> {
  await ensureReady();
  const loc = locate(rootId, relPath);
  if ("error" in loc) return loc.error;
  const real = await realInside(loc.root, loc.abs);
  if (!real.ok) {
    return { ok: false, reason: real.reason, code: real.code };
  }
  return stageFromAbs(real.path, dest);
}

/** 拖拽/粘贴：preload 用 getPathForFile 得到绝对路径后调用（不下发 renderer）。 */
export async function stageFromAbsPath(
  absPath: string,
  dest?: StageDest,
): Promise<FsResult<StagedAttachmentData>> {
  if (!absPath || typeof absPath !== "string") {
    return { ok: false, reason: "无效的请求参数", code: "invalid" };
  }
  return stageFromAbs(absPath, dest);
}

/** MIME → 扩展名（剪贴板图常无可靠 basename）。 */
const MIME_EXT: Record<string, string> = {
  "image/png": ".png",
  "image/jpeg": ".jpg",
  "image/jpg": ".jpg",
  "image/gif": ".gif",
  "image/webp": ".webp",
  "image/bmp": ".bmp",
  "image/avif": ".avif",
  "image/svg+xml": ".svg",
};

function classifyBytes(
  name: string,
  bytes: Uint8Array,
  mime?: string,
): { binary: boolean; text: string; truncated: boolean } {
  const ext = extname(name).toLowerCase();
  if (IMAGE_MIME[ext] || mime?.toLowerCase().startsWith("image/")) {
    return { binary: true, text: "", truncated: false };
  }
  const head = Buffer.from(
    bytes.subarray(0, Math.min(bytes.byteLength, TEXT_PREVIEW_CAP + 1)),
  );
  if (sniffBinary(head)) {
    return { binary: true, text: "", truncated: false };
  }
  const truncated = bytes.byteLength > TEXT_PREVIEW_CAP;
  const text = head
    .subarray(0, Math.min(head.length, TEXT_PREVIEW_CAP))
    .toString("utf-8");
  return { binary: false, text, truncated };
}

/**
 * 无磁盘路径的 File（剪贴板截图 / JS 构造的 File）：按字节驻留。
 * 与 stageFromAbs 同出口（有 dest → attachments/；否则 attach-staging）。
 */
export async function stageFromBytes(
  name: string,
  bytes: Uint8Array,
  dest?: StageDest,
  mime?: string,
): Promise<FsResult<StagedAttachmentData>> {
  if (!(bytes instanceof Uint8Array)) {
    return { ok: false, reason: "无效的请求参数", code: "invalid" };
  }
  if (bytes.byteLength === 0) {
    return { ok: false, reason: "文件内容为空", code: "invalid" };
  }
  if (bytes.byteLength > ATTACH_MAX_BYTES) {
    return {
      ok: false,
      reason: `文件超过 ${Math.round(ATTACH_MAX_BYTES / (1024 * 1024))}MB 上限`,
      code: "invalid",
    };
  }

  let fileName = safeName(name);
  if (!extname(fileName) && mime) {
    const ext = MIME_EXT[mime.toLowerCase()];
    if (ext) fileName = `${fileName}${ext}`;
  }
  const meta = classifyBytes(fileName, bytes, mime);
  const sizeBytes = bytes.byteLength;

  if (dest) {
    const used = await listExistingAttachmentNames(
      dest.rootId,
      dest.subpath || "",
    );
    const deduped = dedupName(fileName, used);
    const destRes = await resolveDestAbs(dest, deduped);
    if (!destRes.ok) return destRes;
    try {
      await withTimeout(
        fs.writeFile(destRes.data, bytes),
        ATTACH_COPY_TIMEOUT_MS,
        "WRITE",
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      if (msg.includes("TIMEOUT")) {
        return { ok: false, reason: UNSYNCED_HINT, code: "busy" };
      }
      return {
        ok: false,
        reason: "写入工作区失败",
        code: "error",
      };
    }
    return {
      ok: true,
      data: {
        name: deduped,
        workspacePath: `${ATTACHMENTS_DIR}/${deduped}`,
        binary: meta.binary,
        text: meta.text,
        truncated: meta.truncated,
        sizeBytes,
      },
    };
  }

  const id = randomUUID();
  const dir = join(stagingDir(), id);
  await fs.mkdir(dir, { recursive: true });
  const stagedAbs = join(dir, fileName);
  try {
    await withTimeout(
      fs.writeFile(stagedAbs, bytes),
      ATTACH_COPY_TIMEOUT_MS,
      "WRITE",
    );
  } catch (e) {
    try {
      await fs.rm(dir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
    const msg = e instanceof Error ? e.message : "";
    if (msg.includes("TIMEOUT")) {
      return { ok: false, reason: UNSYNCED_HINT, code: "busy" };
    }
    return { ok: false, reason: "暂存附件失败", code: "error" };
  }

  staging.set(id, {
    absPath: stagedAbs,
    name: fileName,
    binary: meta.binary,
    text: meta.text,
    truncated: meta.truncated,
    sizeBytes,
  });

  return {
    ok: true,
    data: {
      name: fileName,
      stagingId: id,
      binary: meta.binary,
      text: meta.text,
      truncated: meta.truncated,
      sizeBytes,
    },
  };
}

/** 草稿/云端：把暂存文件写入本地工作区 attachments/。 */
export async function finalizeStagedAttachment(
  stagingId: string,
  dest: StageDest,
): Promise<FsResult<StagedAttachmentData>> {
  const entry = await lookupStaging(stagingId);
  if (!entry) {
    return {
      ok: false,
      reason: "附件暂存已失效，请重新附加",
      code: "not_found",
    };
  }
  const out = await writeToDest(entry, dest, "staged");
  if (out.ok) {
    staging.delete(stagingId);
    try {
      await fs.rm(join(stagingDir(), stagingId), {
        recursive: true,
        force: true,
      });
    } catch {
      /* ignore */
    }
  }
  return out;
}

/** 云端工作区：取出暂存字节供 PUT /workspace/files（取出后清除暂存）。 */
export async function consumeStagedBytes(
  stagingId: string,
): Promise<FsResult<{ name: string; data: Uint8Array; binary: boolean }>> {
  const entry = await lookupStaging(stagingId);
  if (!entry) {
    return {
      ok: false,
      reason: "附件暂存已失效，请重新附加",
      code: "not_found",
    };
  }
  try {
    const buf = await withTimeout(
      fs.readFile(entry.absPath),
      ATTACH_COPY_TIMEOUT_MS,
      "READ",
    );
    staging.delete(stagingId);
    try {
      await fs.rm(join(stagingDir(), stagingId), {
        recursive: true,
        force: true,
      });
    } catch {
      /* ignore */
    }
    return {
      ok: true,
      data: {
        name: entry.name,
        data: new Uint8Array(buf),
        binary: entry.binary,
      },
    };
  } catch (e) {
    const msg = e instanceof Error ? e.message : "";
    if (msg.includes("TIMEOUT")) {
      return { ok: false, reason: UNSYNCED_HINT, code: "busy" };
    }
    return { ok: false, reason: "读取暂存附件失败", code: "error" };
  }
}
