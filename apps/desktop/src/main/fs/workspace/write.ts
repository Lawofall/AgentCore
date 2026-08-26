import { randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
} from "node:path";
import type { WorkspaceOpResult } from "@shared/ipc-contract";
import { shell } from "electron";
import {
  WORKSPACE_EXTRACT_SOURCE_MAX,
  WORKSPACE_READ_HEAD_MAX,
  WORKSPACE_READ_MAX,
} from "../constants";
import { realInside, resolveLexical, toReason } from "../pathGuard";
import type { StoredRoot } from "../roots";
import { TRASH_REL, isInternalZoneRelPath } from "../workspaceIgnore";
import { opErr, opOk } from "./result";
import { isSessionGrantRoot } from "./sessionRoot";
import { applyTextReplace } from "./textReplace";

/** 原子写：同目录临时文件 + rename，避免进程中断在用户真实磁盘上留下半截文件。 */
export async function atomicWrite(abs: string, data: Buffer): Promise<void> {
  const tmp = join(dirname(abs), `.tmp_ws_${randomUUID()}`);
  try {
    await fs.writeFile(tmp, data);
    await fs.rename(tmp, abs);
  } catch (e) {
    await fs.rm(tmp, { force: true }).catch(() => {});
    throw e;
  }
}

/**
 * 解析「目标可不存在」的写入路径并校验在根内（write/write_bytes/mkdir/move 目标用）。
 *
 * 词法定位先拒 `..`/绝对/同名兄弟；再对「最深的已存在祖先」做 realpath 复核，防止经
 * 符号链接祖先逃逸——与服务端 `resolve_safe_path` 的 `.resolve()` 语义对齐（不存在的
 * 尾段无法是符号链接，故只需校验已存在部分）。返回可安全写入的绝对路径，越界返回 null。
 */
export async function resolveWritable(
  root: StoredRoot,
  relPath: string,
): Promise<string | null> {
  const abs = resolveLexical(root, relPath);
  if (!abs) return null;
  let existing = abs;
  const tail: string[] = [];
  for (;;) {
    try {
      await fs.lstat(existing);
      break;
    } catch {
      const parent = dirname(existing);
      if (parent === existing) break; // 抵达文件系统根（根目录必存在，不应触发）
      tail.unshift(basename(existing));
      existing = parent;
    }
  }
  const realExisting = await realInside(root, existing);
  if (!realExisting.ok) return null;
  return tail.length > 0 ? join(realExisting.path, ...tail) : realExisting.path;
}

export async function opReadBytes(
  root: StoredRoot,
  relPath: string,
  maxBytes?: number,
): Promise<WorkspaceOpResult> {
  const abs = resolveLexical(root, relPath);
  if (!abs) return opErr("OutsideWorkspace", relPath);
  let st: import("node:fs").Stats;
  try {
    st = await fs.stat(abs);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") {
      return opErr("PathNotFound", relPath);
    }
    return opErr("WorkspaceIOError", toReason(e));
  }
  if (!st.isFile()) return opErr("NotAFile", relPath);
  const requested =
    typeof maxBytes === "number" && Number.isFinite(maxBytes) && maxBytes >= 1
      ? Math.floor(maxBytes)
      : WORKSPACE_READ_MAX;
  const cap = Math.min(requested, WORKSPACE_EXTRACT_SOURCE_MAX);
  if (st.size > cap) {
    return opErr("WorkspaceIOError", `文件过大，无法读取（${st.size}字节）`);
  }
  const real = await realInside(root, abs);
  if (!real.ok) {
    return real.code === "out_of_root"
      ? opErr("OutsideWorkspace", relPath)
      : opErr("PathNotFound", relPath);
  }
  try {
    // JSON 无字节类型：以 base64 回填，服务端 LocalWorkspace.read_bytes 解码还原。
    return opOk((await fs.readFile(real.path)).toString("base64"));
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
}

export async function opReadHead(
  root: StoredRoot,
  relPath: string,
  maxBytes?: number,
): Promise<WorkspaceOpResult> {
  const abs = resolveLexical(root, relPath);
  if (!abs) return opErr("OutsideWorkspace", relPath);
  let st: import("node:fs").Stats;
  try {
    st = await fs.stat(abs);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") {
      return opErr("PathNotFound", relPath);
    }
    return opErr("WorkspaceIOError", toReason(e));
  }
  if (!st.isFile()) return opErr("NotAFile", relPath);
  const requested =
    typeof maxBytes === "number" && Number.isFinite(maxBytes) && maxBytes >= 1
      ? Math.floor(maxBytes)
      : WORKSPACE_READ_HEAD_MAX;
  const cap = Math.min(requested, WORKSPACE_READ_HEAD_MAX);
  const n = Math.min(cap, st.size);
  const real = await realInside(root, abs);
  if (!real.ok) {
    return real.code === "out_of_root"
      ? opErr("OutsideWorkspace", relPath)
      : opErr("PathNotFound", relPath);
  }
  let fh: import("node:fs/promises").FileHandle | undefined;
  try {
    fh = await fs.open(real.path, "r");
    const buf = Buffer.alloc(n);
    if (n > 0) {
      await fh.read(buf, 0, n, 0);
    }
    return opOk({ data: buf.toString("base64"), size_bytes: st.size });
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  } finally {
    await fh?.close().catch(() => {});
  }
}

export async function opWrite(
  root: StoredRoot,
  relPath: string,
  content: string,
): Promise<WorkspaceOpResult> {
  const target = await resolveWritable(root, relPath);
  if (!target) return opErr("OutsideWorkspace", relPath);
  if (target === root.absPath) return opErr("WorkspaceIOError", "目标是目录");
  try {
    await fs.mkdir(dirname(target), { recursive: true });
    await atomicWrite(target, Buffer.from(content, "utf-8"));
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  return opOk([...content].length); // 码点数，与服务端 len(content) 对齐
}

export async function opAppend(
  root: StoredRoot,
  relPath: string,
  content: string,
): Promise<WorkspaceOpResult> {
  const target = await resolveWritable(root, relPath);
  if (!target) return opErr("OutsideWorkspace", relPath);
  if (target === root.absPath) return opErr("WorkspaceIOError", "目标是目录");
  try {
    await fs.mkdir(dirname(target), { recursive: true });
    let exists = false;
    try {
      const st = await fs.stat(target);
      if (!st.isFile()) return opErr("NotAFile", relPath);
      exists = true;
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "ENOENT") {
        return opErr("WorkspaceIOError", toReason(e));
      }
    }
    if (exists) {
      await fs.appendFile(target, content, "utf-8");
    } else {
      await atomicWrite(target, Buffer.from(content, "utf-8"));
    }
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  return opOk([...content].length);
}

export async function opWriteBytes(
  root: StoredRoot,
  relPath: string,
  base64Data: string,
): Promise<WorkspaceOpResult> {
  const target = await resolveWritable(root, relPath);
  if (!target) return opErr("OutsideWorkspace", relPath);
  if (target === root.absPath) return opErr("WorkspaceIOError", "目标是目录");
  const data = Buffer.from(base64Data, "base64");
  try {
    await fs.mkdir(dirname(target), { recursive: true });
    await atomicWrite(target, data);
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  return opOk(data.length);
}

export async function opMkdir(
  root: StoredRoot,
  relPath: string,
): Promise<WorkspaceOpResult> {
  const target = await resolveWritable(root, relPath);
  if (!target) return opErr("OutsideWorkspace", relPath);
  if (target === root.absPath) return opErr("OutsideWorkspace", relPath); // 根已存在
  try {
    await fs.lstat(target);
    return opErr("AlreadyExists", relPath);
  } catch {
    // 不存在 —— 符合预期
  }
  try {
    await fs.mkdir(target, { recursive: true });
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  return opOk(null);
}

/** 无系统回收站时：移入工作区 `AgentCore/trash/<id>/` 并写 meta（对齐服务端 soft_delete）。 */
async function softDeleteToWorkspaceTrash(
  root: StoredRoot,
  realPath: string,
  relPath: string,
): Promise<WorkspaceOpResult> {
  if (trashDestUnderTarget(root.absPath, realPath)) {
    return opErr(
      "WorkspaceIOError",
      "不能软删到自身子树内的回收区（会自嵌套）",
    );
  }
  const entryId = randomUUID().replace(/-/g, "");
  const entryDir = join(root.absPath, ...TRASH_REL.split("/"), entryId);
  const dest = join(entryDir, "content");
  try {
    const st = await fs.lstat(realPath);
    await fs.mkdir(entryDir, { recursive: true });
    await fs.rename(realPath, dest);
    const meta = {
      original_path: relPath.replace(/\\/g, "/"),
      deleted_at: new Date().toISOString(),
      is_dir: st.isDirectory(),
      name: basename(realPath),
    };
    await fs.writeFile(
      join(entryDir, "meta.json"),
      `${JSON.stringify(meta, null, 2)}\n`,
      "utf-8",
    );
    return opOk(null);
  } catch (e) {
    await fs.rm(entryDir, { recursive: true, force: true }).catch(() => {});
    return opErr("WorkspaceIOError", toReason(e));
  }
}

/** True when AgentCore/trash would land inside ``targetAbs`` (self-nest risk). */
function trashDestUnderTarget(rootAbs: string, targetAbs: string): boolean {
  const trashRoot = resolve(join(rootAbs, ...TRASH_REL.split("/")));
  const target = resolve(targetAbs);
  const rel = relative(target, trashRoot);
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

function childRelFromRoot(rootAbs: string, childAbs: string): string {
  return relative(rootAbs, childAbs).replace(/\\/g, "/");
}

/**
 * Soft-delete a trash ancestor (e.g. bare AgentCore/) by expanding children —
 * hard-clear internal zones first, then soft-delete each visible child.
 */
async function softDeleteExpandingTrashAncestor(
  root: StoredRoot,
  targetAbs: string,
): Promise<WorkspaceOpResult> {
  let names: string[];
  try {
    names = await fs.readdir(targetAbs);
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }

  const zoneChildren: string[] = [];
  const softChildren: string[] = [];
  for (const name of names) {
    const childAbs = join(targetAbs, name);
    const childRel = childRelFromRoot(root.absPath, childAbs);
    if (isInternalZoneRelPath(childRel)) {
      zoneChildren.push(childAbs);
    } else {
      softChildren.push(childAbs);
    }
  }

  for (const childAbs of zoneChildren) {
    try {
      await fs.rm(childAbs, { recursive: true, force: false });
    } catch (e) {
      return opErr("WorkspaceIOError", toReason(e));
    }
  }

  for (const childAbs of softChildren) {
    const childRel = childRelFromRoot(root.absPath, childAbs);
    try {
      await shell.trashItem(childAbs);
    } catch {
      const soft = await softDeleteToWorkspaceTrash(root, childAbs, childRel);
      if (!soft.ok) return soft;
    }
  }

  // Soft-deletes recreate AgentCore/trash under the ancestor — leave the shell.
  try {
    const remaining = await fs.readdir(targetAbs);
    const leftovers: string[] = [];
    for (const name of remaining) {
      const childRel = childRelFromRoot(root.absPath, join(targetAbs, name));
      if (!isInternalZoneRelPath(childRel)) {
        leftovers.push(name);
      }
    }
    if (leftovers.length > 0) {
      return opErr(
        "WorkspaceIOError",
        `软删展开后目录仍有残留：${leftovers.slice(0, 5).join(", ")}`,
      );
    }
    return opOk(null);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") {
      return opOk(null);
    }
    return opErr("WorkspaceIOError", toReason(e));
  }
}

export async function opDelete(
  root: StoredRoot,
  relPath: string,
  permanent = false,
): Promise<WorkspaceOpResult> {
  const abs = resolveLexical(root, relPath);
  if (!abs) return opErr("OutsideWorkspace", relPath);
  if (abs === root.absPath) return opErr("OutsideWorkspace", relPath); // 不删根
  try {
    await fs.lstat(abs);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") {
      return opErr("PathNotFound", relPath);
    }
    return opErr("WorkspaceIOError", toReason(e));
  }
  const real = await realInside(root, abs);
  if (!real.ok) {
    return real.code === "out_of_root"
      ? opErr("OutsideWorkspace", relPath)
      : opErr("PathNotFound", relPath);
  }
  try {
    // 会话授权根（区外目录）一律可逆：内部区硬删是产品自己工作区的语义，用户文件夹下的
    // `AgentCore/{index,trash,baselines}` 是用户自己的东西，普通 delete 不得递归永久删。
    // 系统回收站不可用时诚实报错——不在用户文件夹里新建 `AgentCore/trash/` 落盘。
    if (isSessionGrantRoot(root)) {
      try {
        await shell.trashItem(real.path);
        return opOk(null);
      } catch (e) {
        return opErr(
          "WorkspaceIOError",
          `系统回收站不可用，已放弃删除（区外目录只做可逆删除，不在你的文件夹里建软删区）：${toReason(e)}`,
        );
      }
    }
    // Hard-delete only for internal zones (index/trash/baselines) — not whole AgentCore/.
    const hard = permanent || isInternalZoneRelPath(relPath);
    if (hard) {
      await fs.rm(real.path, { recursive: true, force: false });
      return opOk(null);
    }
    if (trashDestUnderTarget(root.absPath, real.path)) {
      return softDeleteExpandingTrashAncestor(root, real.path);
    }
    // 默认可逆：系统回收站；失败则落工作区软删区（无回收站 / 权限拒绝等）。
    try {
      await shell.trashItem(real.path);
      return opOk(null);
    } catch {
      return softDeleteToWorkspaceTrash(root, real.path, relPath);
    }
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
}

/**
 * Cloud scratch → organize: dest is local; src bytes ride COPY (not write_bytes).
 * Same no-overwrite / pathGuard as path-to-path copy.
 */
export async function opCopyFromBytes(
  dstRoot: StoredRoot,
  dst: string,
  base64Data: string,
): Promise<WorkspaceOpResult> {
  const dstTarget = await resolveWritable(dstRoot, dst);
  if (!dstTarget) return opErr("OutsideWorkspace", dst);
  if (dstTarget === dstRoot.absPath) return opErr("OutsideWorkspace", dst);
  let dstExists = true;
  try {
    await fs.lstat(dstTarget);
  } catch {
    dstExists = false;
  }
  if (dstExists) return opErr("AlreadyExists", dst);
  try {
    await fs.mkdir(dirname(dstTarget), { recursive: true });
    await atomicWrite(dstTarget, Buffer.from(base64Data, "base64"));
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  return opOk(null);
}

/**
 * Copy ``src`` under ``srcRoot`` to ``dst`` under ``dstRoot``.
 *
 * Same-root copy passes the same object twice. Cross-root (workspace → organize)
 * still runs ``resolveLexical`` / ``realInside`` on the source root and
 * ``resolveWritable`` on the dest root — same algorithms, separate roots.
 */
export async function opCopy(
  srcRoot: StoredRoot,
  dstRoot: StoredRoot,
  src: string,
  dst: string,
): Promise<WorkspaceOpResult> {
  const srcAbs = resolveLexical(srcRoot, src);
  if (!srcAbs) return opErr("OutsideWorkspace", src);
  if (srcAbs === srcRoot.absPath) return opErr("OutsideWorkspace", src);
  try {
    await fs.lstat(srcAbs);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") {
      return opErr("PathNotFound", src);
    }
    return opErr("WorkspaceIOError", toReason(e));
  }
  const srcReal = await realInside(srcRoot, srcAbs);
  if (!srcReal.ok) {
    return srcReal.code === "out_of_root"
      ? opErr("OutsideWorkspace", src)
      : opErr("PathNotFound", src);
  }

  const dstTarget = await resolveWritable(dstRoot, dst);
  if (!dstTarget) return opErr("OutsideWorkspace", dst);
  if (dstTarget === dstRoot.absPath) return opErr("OutsideWorkspace", dst);

  // 禁止把目录复制进自身或其子树（否则 fs.cp 会自我递归）。
  const intoRel = relative(srcReal.path, dstTarget);
  if (intoRel === "" || (!intoRel.startsWith("..") && !isAbsolute(intoRel))) {
    return opErr("WorkspaceIOError", "不能复制到自身或其子目录");
  }

  let dstExists = true;
  try {
    await fs.lstat(dstTarget);
  } catch {
    dstExists = false;
  }
  if (dstExists) return opErr("AlreadyExists", dst);
  try {
    await fs.mkdir(dirname(dstTarget), { recursive: true });
    await fs.cp(srcReal.path, dstTarget, {
      recursive: true,
      errorOnExist: true,
    });
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  return opOk(null);
}

export async function opMove(
  root: StoredRoot,
  src: string,
  dst: string,
): Promise<WorkspaceOpResult> {
  const srcAbs = resolveLexical(root, src);
  if (!srcAbs) return opErr("OutsideWorkspace", src);
  if (srcAbs === root.absPath) return opErr("OutsideWorkspace", src);
  try {
    await fs.lstat(srcAbs);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") {
      return opErr("PathNotFound", src);
    }
    return opErr("WorkspaceIOError", toReason(e));
  }
  const srcReal = await realInside(root, srcAbs);
  if (!srcReal.ok) {
    return srcReal.code === "out_of_root"
      ? opErr("OutsideWorkspace", src)
      : opErr("PathNotFound", src);
  }

  const dstTarget = await resolveWritable(root, dst);
  if (!dstTarget) return opErr("OutsideWorkspace", dst);
  if (dstTarget === root.absPath) return opErr("OutsideWorkspace", dst);
  let dstExists = true;
  try {
    await fs.lstat(dstTarget);
  } catch {
    dstExists = false;
  }
  if (dstExists) return opErr("AlreadyExists", dst);
  try {
    await fs.mkdir(dirname(dstTarget), { recursive: true });
    await fs.rename(srcReal.path, dstTarget);
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  return opOk(null);
}

export async function opReplace(
  root: StoredRoot,
  relPath: string,
  oldStr: string,
  newStr: string,
  all: boolean,
): Promise<WorkspaceOpResult> {
  const abs = resolveLexical(root, relPath);
  if (!abs) return opErr("OutsideWorkspace", relPath);
  try {
    await fs.lstat(abs);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") {
      return opErr("PathNotFound", relPath);
    }
    return opErr("WorkspaceIOError", toReason(e));
  }
  const real = await realInside(root, abs);
  if (!real.ok) {
    return real.code === "out_of_root"
      ? opErr("OutsideWorkspace", relPath)
      : opErr("PathNotFound", relPath);
  }
  let st: import("node:fs").Stats;
  try {
    st = await fs.stat(real.path);
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  if (!st.isFile()) return opErr("NotAFile", relPath);

  let content: string;
  try {
    const buf = await fs.readFile(real.path);
    // fatal 解码：非法 UTF-8 抛 TypeError → NotUTF8（对齐服务端 read_bytes().decode）。
    content = new TextDecoder("utf-8", { fatal: true }).decode(buf);
  } catch (e) {
    if (e instanceof TypeError) return opErr("NotUTF8", relPath);
    return opErr("WorkspaceIOError", toReason(e));
  }

  // Exact first, then LF-normalized fallback (mirrors server text_replace.py).
  const replaced = applyTextReplace(content, oldStr, newStr, all);
  if (!replaced.ok) {
    if (replaced.kind === "AmbiguousMatch") {
      return opErr(
        "AmbiguousMatch",
        `${replaced.count} matches`,
        replaced.count,
      );
    }
    return opErr("NoMatch", relPath);
  }
  try {
    await atomicWrite(real.path, Buffer.from(replaced.content, "utf-8"));
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  return opOk({ count: replaced.count, first_line: replaced.firstLine });
}
