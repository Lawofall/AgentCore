import { promises as fs } from "node:fs";
import { basename, dirname, isAbsolute, join, relative } from "node:path";
import type {
  FsCreateKind,
  FsEntry,
  FsFileRef,
  FsListFilesOrder,
  FsListFilesResult,
  FsResult,
} from "@shared/ipc-contract";
import { LIST_FILES_CAP, LIST_FILES_MAX_DEPTH } from "./constants";
import { loadIgnore } from "./loadIgnore";
import { fromErrno, fsErr, locate, realFail, realInside } from "./pathGuard";
import { ensureReady } from "./roots";
import { resolveWritable } from "./workspace/write";
import {
  AGENTCORE_ROOT,
  shouldSkipSystemWorkspaceEntry,
  shouldSkipWorkspaceEntry,
} from "./workspaceIgnore";

/** Keep in sync with server `stage_dirs.RULES_DIR_NAME`. */
const RULES_DIR_NAME = "rules";
const LEGACY_RULES_DIR_NAME = "规则";
/** One-shot rename source. Not a second live root. */
const LEGACY_AGENTCORE_ROOT = "AgentCore";

export async function renameLegacyRulesLeaf(
  workspaceRoot: string,
): Promise<void> {
  const legacyRoot = join(workspaceRoot, LEGACY_AGENTCORE_ROOT);
  const nextRoot = join(workspaceRoot, AGENTCORE_ROOT);
  try {
    await fs.access(nextRoot);
  } catch {
    try {
      const st = await fs.stat(legacyRoot);
      if (st.isDirectory()) await fs.rename(legacyRoot, nextRoot);
    } catch {
      // missing legacy root, or rename lost a race with the new root
    }
  }
  const src = join(workspaceRoot, AGENTCORE_ROOT, LEGACY_RULES_DIR_NAME);
  const dst = join(workspaceRoot, AGENTCORE_ROOT, RULES_DIR_NAME);
  try {
    await fs.rename(src, dst);
    return;
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code;
    if (code === "ENOENT") return;
    if (code !== "EEXIST" && code !== "ENOTEMPTY") return;
  }
  try {
    const kids = await fs.readdir(src, { withFileTypes: true });
    for (const k of kids) {
      const from = join(src, k.name);
      const to = join(dst, k.name);
      try {
        await fs.stat(to);
      } catch {
        await fs.rename(from, to);
      }
    }
    if ((await fs.readdir(src)).length === 0) await fs.rmdir(src);
  } catch {
    // best-effort one-shot; leftover 规则/ is still a visible folder
  }
}

/** 扁平收集结果：相对路径 + 可选本机指纹（stat，非全文 hash）。 */
export type CollectedWorkspaceFile = FsFileRef & {
  mtimeMs: number;
  sizeBytes: number;
};

/** `order: "recent"` 收齐路径后的并发 stat 上限；默认 path 序不 stat。 */
const STAT_CONCURRENCY = 32;

type WalkedFile = CollectedWorkspaceFile & { abs: string };

async function fillMtimesLimited(
  files: WalkedFile[],
  concurrency: number,
): Promise<void> {
  if (files.length === 0) return;
  let next = 0;
  const worker = async () => {
    while (true) {
      const i = next;
      next += 1;
      if (i >= files.length) return;
      const item = files[i];
      try {
        const st = await fs.stat(item.abs);
        item.mtimeMs = st.mtimeMs;
        item.sizeBytes = st.size;
      } catch {
        // unreadable stat → zeros; recent sort sinks to the bottom
      }
    }
  };
  const n = Math.min(concurrency, files.length);
  await Promise.all(Array.from({ length: n }, () => worker()));
}

/**
 * 工作区扁平文件索引（共享走法）：广度优先逐层展开 `real` 根，受深度（`LIST_FILES_MAX_DEPTH`）
 * 与总数（`LIST_FILES_CAP`）双重限制；跳过依赖/构建/VCS 目录与根 `.gitignore`，不跟随符号链接
 * （避免环路与越界）。`truncated` 表示命中 cap 截断。@ 提及检索（`listFiles`）与 worker
 * 工作区清单（`opIndexFiles`）共用同一套走法，使本地根与云端 `ServerWorkspace.index_files`
 * 呈现一致的扁平视图。
 *
 * `order`：`"path"`（默认）= 字母序且不 stat；`"recent"` = 按 mtime 倒序。
 * `fingerprint`：为 true 时对本机文件当场 `stat` 填 `mtimeMs`/`sizeBytes`（供服务端跳过未变文件）。
 * @ 提及走法默认 false。`recent` 收齐路径后以有限并发 stat（避免 OneDrive 占位上数千次串行）；
 * 不是二段式异步加载。文件/目录判别只信 dirent，不为此 stat 全量。
 * `cap`：覆盖 `LIST_FILES_CAP`（测试 / 有界扫描）；未传则用默认上限。
 */
export async function collectWorkspaceFiles(
  real: string,
  order: "path" | "recent" = "path",
  opts?: { fingerprint?: boolean; cap?: number },
): Promise<{ files: CollectedWorkspaceFile[]; truncated: boolean }> {
  await renameLegacyRulesLeaf(real);
  const recent = order === "recent";
  const fingerprint = opts?.fingerprint === true;
  const cap = opts?.cap ?? LIST_FILES_CAP;
  const ig = await loadIgnore(real);
  const collected: WalkedFile[] = [];
  let truncated = false;
  const stack: Array<{ abs: string; rel: string; depth: number }> = [
    { abs: real, rel: "", depth: 0 },
  ];
  while (stack.length > 0) {
    if (collected.length >= cap) {
      truncated = true;
      break;
    }
    const cur = stack.pop();
    if (!cur) break;
    let dirents: import("node:fs").Dirent[];
    try {
      dirents = await fs.readdir(cur.abs, { withFileTypes: true });
    } catch {
      continue; // 单个子目录不可读不影响整体
    }
    for (const d of dirents) {
      if (d.isSymbolicLink()) continue;
      const childRel = cur.rel ? `${cur.rel}/${d.name}` : d.name;
      if (d.isDirectory()) {
        if (shouldSkipWorkspaceEntry(d.name, true, cur.rel)) continue;
        if (ig.ignores(`${childRel}/`)) continue;
        if (cur.depth + 1 <= LIST_FILES_MAX_DEPTH) {
          stack.push({
            abs: join(cur.abs, d.name),
            rel: childRel,
            depth: cur.depth + 1,
          });
        }
      } else if (d.isFile()) {
        if (shouldSkipWorkspaceEntry(d.name, false, cur.rel)) continue;
        if (ig.ignores(childRel)) continue;
        const abs = join(cur.abs, d.name);
        let mtimeMs = 0;
        let sizeBytes = 0;
        // fingerprint 当场 stat（worker 清单要 size）；path 序不 stat。
        // recent 收齐后再有限并发 stat，见 walk 结束后的 fillMtimesLimited。
        if (fingerprint) {
          try {
            const st = await fs.stat(abs);
            mtimeMs = st.mtimeMs;
            sizeBytes = st.size;
          } catch {
            // unreadable stat → zeros
          }
        }
        collected.push({
          relPath: childRel,
          name: d.name,
          mtimeMs,
          sizeBytes,
          abs,
        });
        if (collected.length >= cap) {
          truncated = true;
          break;
        }
      }
    }
  }
  if (recent && !fingerprint) {
    await fillMtimesLimited(collected, STAT_CONCURRENCY);
  }
  if (recent) {
    collected.sort((a, b) => b.mtimeMs - a.mtimeMs); // newest first
  } else {
    collected.sort((a, b) => a.relPath.localeCompare(b.relPath, "zh"));
  }
  return {
    files: collected.map(({ relPath, name, mtimeMs, sizeBytes }) => ({
      relPath,
      name,
      mtimeMs,
      sizeBytes,
    })),
    truncated,
  };
}

export async function listDir(
  rootId: string,
  relPath: string,
): Promise<FsResult<FsEntry[]>> {
  await ensureReady();
  const loc = locate(rootId, relPath);
  if ("error" in loc) return loc.error;
  await renameLegacyRulesLeaf(loc.root.absPath);
  const real = await realInside(loc.root, loc.abs);
  if (!real.ok) return realFail(real);
  try {
    const dirents = await fs.readdir(real.path, { withFileTypes: true });
    const entries: FsEntry[] = [];
    for (const d of dirents) {
      const isDir = d.isDirectory();
      // 文件 UI：仅系统噪音；媒体/压缩包等 AI 噪音仍可见（交付物）。
      if (shouldSkipSystemWorkspaceEntry(d.name, isDir, relPath)) continue;
      const childRel = relPath ? `${relPath}/${d.name}` : d.name;
      let size: number | null = null;
      let modifiedMs: number | null = null;
      try {
        const st = await fs.stat(join(real.path, d.name));
        size = isDir ? null : st.size;
        modifiedMs = st.mtimeMs;
      } catch {
        // 单个项 stat 失败（如失效符号链接）不影响整体列举
      }
      entries.push({
        name: d.name,
        relPath: childRel,
        kind: isDir ? "dir" : "file",
        size,
        modifiedMs,
      });
    }
    entries.sort((a, b) =>
      a.kind === b.kind
        ? a.name.localeCompare(b.name, "zh")
        : a.kind === "dir"
          ? -1
          : 1,
    );
    return { ok: true, data: entries };
  } catch (e) {
    return fromErrno(e);
  }
}

export async function listFiles(
  rootId: string,
  opts?: { order?: FsListFilesOrder; cap?: number },
): Promise<FsResult<FsListFilesResult>> {
  await ensureReady();
  const loc = locate(rootId, "");
  if ("error" in loc) return loc.error;
  const real = await realInside(loc.root, loc.abs);
  if (!real.ok) return realFail(real);
  try {
    const order = opts?.order ?? "path";
    const recent = order === "recent";
    const { files, truncated } = await collectWorkspaceFiles(
      real.path,
      order,
      opts?.cap != null ? { cap: opts.cap } : undefined,
    );
    return {
      ok: true,
      data: {
        files: files.map(({ relPath, name, mtimeMs }) =>
          recent ? { relPath, name, mtimeMs } : { relPath, name },
        ),
        truncated,
      },
    };
  } catch (e) {
    return fromErrno(e);
  }
}

export function isValidName(name: string): boolean {
  return (
    name.length > 0 &&
    name !== "." &&
    name !== ".." &&
    !name.includes("/") &&
    !name.includes("\\")
  );
}

export async function rename(
  rootId: string,
  relPath: string,
  newName: string,
): Promise<FsResult> {
  await ensureReady();
  if (!isValidName(newName)) return fsErr("invalid", "名称非法");
  if (!relPath) return fsErr("invalid", "不能重命名根目录");
  const loc = locate(rootId, relPath);
  if ("error" in loc) return loc.error;
  const srcReal = await realInside(loc.root, loc.abs);
  if (!srcReal.ok) return realFail(srcReal);
  const destAbs = join(dirname(loc.abs), newName);
  const destRel = relative(loc.root.absPath, destAbs);
  if (destRel.startsWith("..") || isAbsolute(destRel)) {
    return fsErr("out_of_root", "目标越界，已拒绝");
  }
  try {
    await fs.access(destAbs);
    return fsErr("exists", "同名文件已存在");
  } catch {
    // 目标不存在 —— 符合预期
  }
  try {
    await fs.rename(srcReal.path, destAbs);
    return { ok: true, data: undefined };
  } catch (e) {
    return fromErrno(e);
  }
}

export async function move(
  rootId: string,
  srcRelPath: string,
  destDirRelPath: string,
): Promise<FsResult> {
  await ensureReady();
  if (!srcRelPath) return fsErr("invalid", "不能移动根目录");
  const srcLoc = locate(rootId, srcRelPath);
  if ("error" in srcLoc) return srcLoc.error;
  const destLoc = locate(rootId, destDirRelPath);
  if ("error" in destLoc) return destLoc.error;

  const srcReal = await realInside(srcLoc.root, srcLoc.abs);
  if (!srcReal.ok) return realFail(srcReal);

  // 目标目录可不存在：resolveWritable + mkdir recursive（懒物化工作区首写粘贴）。
  const destDirTarget = await resolveWritable(destLoc.root, destDirRelPath);
  if (!destDirTarget) return fsErr("out_of_root", "目标越界，已拒绝");
  try {
    await fs.mkdir(destDirTarget, { recursive: true });
  } catch (e) {
    return fromErrno(e);
  }
  const destDirCheck = await realInside(destLoc.root, destDirTarget);
  if (!destDirCheck.ok) return realFail(destDirCheck);
  try {
    const st = await fs.stat(destDirCheck.path);
    if (!st.isDirectory()) return fsErr("invalid", "目标不是目录");
  } catch (e) {
    return fromErrno(e);
  }

  // 禁止把目录移动进自身或其子树
  const intoRel = relative(srcReal.path, destDirCheck.path);
  if (intoRel === "" || (!intoRel.startsWith("..") && !isAbsolute(intoRel))) {
    return fsErr("invalid", "不能移动到自身或其子目录");
  }

  const destAbs = join(destDirCheck.path, basename(srcReal.path));
  try {
    await fs.access(destAbs);
    return fsErr("exists", "目标位置已存在同名项");
  } catch {
    // 目标不存在 —— 符合预期
  }
  try {
    await fs.rename(srcReal.path, destAbs);
    return { ok: true, data: undefined };
  } catch (e) {
    return fromErrno(e);
  }
}

/**
 * 复制文件/目录到**完整目标路径** `destRelPath`（与 move 收「目标目录」不同，copy 收含
 * 最终名的完整路径，故能在同目录内另存为新名——去重粘贴所需）。`fs.cp(recursive)` 递归
 * 复制；拒绝复制根、覆盖已存在目标、以及把目录复制进自身或其子树（否则会自我递归）。
 */
export async function copy(
  rootId: string,
  srcRelPath: string,
  destRelPath: string,
): Promise<FsResult> {
  await ensureReady();
  if (!srcRelPath) return fsErr("invalid", "不能复制根目录");
  const srcLoc = locate(rootId, srcRelPath);
  if ("error" in srcLoc) return srcLoc.error;
  const srcReal = await realInside(srcLoc.root, srcLoc.abs);
  if (!srcReal.ok) return realFail(srcReal);

  // 目标可不存在：经 resolveWritable 校验在根内（含对已存在祖先的 realpath 复核）。
  const dstTarget = await resolveWritable(srcLoc.root, destRelPath);
  if (!dstTarget) return fsErr("out_of_root", "目标越界，已拒绝");
  if (dstTarget === srcLoc.root.absPath) {
    return fsErr("invalid", "不能覆盖根目录");
  }

  // 禁止把目录复制进自身或其子树（否则 fs.cp 会自我递归）。文件复制为同名兄弟不受影响。
  const intoRel = relative(srcReal.path, dstTarget);
  if (intoRel === "" || (!intoRel.startsWith("..") && !isAbsolute(intoRel))) {
    return fsErr("invalid", "不能复制到自身或其子目录");
  }

  try {
    await fs.access(dstTarget);
    return fsErr("exists", "目标位置已存在同名项");
  } catch {
    // 目标不存在 —— 符合预期
  }
  try {
    await fs.mkdir(dirname(dstTarget), { recursive: true });
    await fs.cp(srcReal.path, dstTarget, {
      recursive: true,
      errorOnExist: true,
    });
    return { ok: true, data: undefined };
  } catch (e) {
    return fromErrno(e);
  }
}

export async function create(
  rootId: string,
  relPath: string,
  kind: FsCreateKind,
): Promise<FsResult> {
  await ensureReady();
  const name = basename(relPath);
  if (!isValidName(name)) return fsErr("invalid", "名称非法");
  const loc = locate(rootId, relPath);
  if ("error" in loc) return loc.error;

  // 目标可不存在：resolveWritable + 父目录 mkdir recursive（懒物化工作区首写）。
  const target = await resolveWritable(loc.root, relPath);
  if (!target) return fsErr("out_of_root", "路径越界，已拒绝");
  if (target === loc.root.absPath) {
    return fsErr("invalid", "不能覆盖根目录");
  }

  try {
    await fs.access(target);
    return fsErr("exists", "已存在同名项");
  } catch {
    // 不存在 —— 符合预期
  }
  try {
    await fs.mkdir(dirname(target), { recursive: true });
    if (kind === "dir") {
      await fs.mkdir(target);
    } else {
      const fh = await fs.open(target, "wx");
      await fh.close();
    }
    return { ok: true, data: undefined };
  } catch (e) {
    return fromErrno(e);
  }
}

export async function remove(
  rootId: string,
  relPath: string,
): Promise<FsResult> {
  await ensureReady();
  if (!relPath) return fsErr("invalid", "不能删除根目录");
  const loc = locate(rootId, relPath);
  if ("error" in loc) return loc.error;
  const real = await realInside(loc.root, loc.abs);
  if (!real.ok) return realFail(real);
  try {
    await fs.rm(real.path, { recursive: true, force: false });
    return { ok: true, data: undefined };
  } catch (e) {
    return fromErrno(e);
  }
}
