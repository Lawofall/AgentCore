import { promises as fs } from "node:fs";
import { join, relative } from "node:path";
import type { WorkspaceOpResult } from "@shared/ipc-contract";
import {
  LIST_FILES_CAP,
  LIST_FILES_MAX_DEPTH,
  WORKSPACE_LIST_MAX,
  WORKSPACE_READ_MAX,
} from "../constants";
import { realInside, resolveLexical, toReason } from "../pathGuard";
import type { StoredRoot } from "../roots";
import { collectWorkspaceFiles } from "../tree";
import {
  type AiListSkipOptions,
  shouldSkipAiListEntry,
} from "../workspaceIgnore";
import { globToRegExp, opErr, opOk, toPosix } from "./result";

function isAccessDeniedError(e: unknown): boolean {
  const code = (e as NodeJS.ErrnoException)?.code;
  return code === "EACCES" || code === "EPERM" || code === "EBUSY";
}

/** Existence check — not AI-noise filtered (residency / oracles). */
export async function opExists(
  root: StoredRoot,
  relPath: string,
): Promise<WorkspaceOpResult> {
  const abs = resolveLexical(root, relPath);
  if (!abs) return opErr("OutsideWorkspace", relPath);
  const real = await realInside(root, abs);
  if (!real.ok) {
    if (real.code === "out_of_root") {
      return opErr("OutsideWorkspace", relPath);
    }
    return opOk(false);
  }
  try {
    const st = await fs.stat(real.path);
    return opOk(st.isFile());
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") {
      return opOk(false);
    }
    return opErr("WorkspaceIOError", toReason(e));
  }
}

export async function opRead(
  root: StoredRoot,
  relPath: string,
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
  if (st.size > WORKSPACE_READ_MAX)
    return opErr("WorkspaceIOError", "文件过大，无法读取");
  const real = await realInside(root, abs);
  if (!real.ok) {
    return real.code === "out_of_root"
      ? opErr("OutsideWorkspace", relPath)
      : opErr("PathNotFound", relPath);
  }
  try {
    const buf = await fs.readFile(real.path);
    if (buf.includes(0))
      return opErr("WorkspaceIOError", "二进制文件，无法以文本读取");
    return opOk(buf.toString("utf-8"));
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
}

/**
 * 列举一个目录（`pattern` 含 `**` 则递归）。
 *
 * 返回 `{ entries, truncated }`：条数上限存在就必须说出来——静默切掉的树在用户/模型
 * 眼里就是「文件没了」。多收一条再切，以此判断是否真有剩余。
 */
export async function opList(
  root: StoredRoot,
  directory: string,
  pattern: string,
  revealPaths?: ReadonlySet<string>,
  listOptions?: AiListSkipOptions,
  cap?: number,
): Promise<WorkspaceOpResult> {
  const baseAbs = resolveLexical(root, directory);
  if (!baseAbs) return opErr("OutsideWorkspace", directory);
  const baseReal = await realInside(root, baseAbs);
  // 裸聊懒建尚未 mkdir 时 list 与 index_files 同口径：子树不存在 → 空列表；
  // 路径存在但非目录 → NotADirectory；越界仍硬错。
  if (!baseReal.ok) {
    if (baseReal.code === "out_of_root") {
      return opErr("OutsideWorkspace", directory);
    }
    return opOk({ entries: [], truncated: false });
  }
  let baseStat: import("node:fs").Stats | undefined;
  try {
    baseStat = await fs.stat(baseReal.path);
  } catch {
    baseStat = undefined;
  }
  if (!baseStat?.isDirectory()) {
    return opErr("NotADirectory", directory);
  }

  const recursive = pattern.includes("**");
  const re = globToRegExp(pattern);
  const limit = Math.min(
    cap && cap > 0 ? Math.floor(cap) : WORKSPACE_LIST_MAX,
    LIST_FILES_CAP,
  );
  // Collect one past the limit so "hit the cap" can be told from "that's all".
  const probe = limit + 1;
  type ListEntry = {
    path: string;
    is_dir: boolean;
    size_bytes: number | null;
    mtime_ms: number | null;
  };
  const results: ListEntry[] = [];
  const listBaseRel = (() => {
    const d = directory.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    return d === "." ? "" : d;
  })();

  const walk = async (
    absDir: string,
    relFromBase: string,
    depth: number,
  ): Promise<void> => {
    if (results.length >= probe) return;
    let dirents: import("node:fs").Dirent[];
    try {
      dirents = await fs.readdir(absDir, { withFileTypes: true });
    } catch {
      // Per-subdir unreadability: skip; do not fail the whole list.
      return;
    }
    dirents.sort((a, b) => a.name.localeCompare(b.name));
    for (const d of dirents) {
      if (results.length >= probe) break;
      const isDir = d.isDirectory();
      const parentRel = relFromBase
        ? listBaseRel
          ? `${listBaseRel}/${relFromBase}`
          : relFromBase
        : listBaseRel;
      // Name-first dir ignore (locked ``.pytest_tmp`` etc.) before trusting type.
      // AI list: attachments/ + reveal_paths + external/archives exemptions.
      if (
        shouldSkipAiListEntry(d.name, true, parentRel, revealPaths, listOptions)
      )
        continue;
      if (
        !isDir &&
        shouldSkipAiListEntry(
          d.name,
          false,
          parentRel,
          revealPaths,
          listOptions,
        )
      )
        continue;
      const childRel = relFromBase ? `${relFromBase}/${d.name}` : d.name;
      if (re.test(childRel)) {
        const childAbs = join(absDir, d.name);
        // Symmetry with server DirEntry: file size + mtime_ms; dirs size null.
        // Per-entry stat failure → empty metadata, do not fail the whole list.
        let size_bytes: number | null = null;
        let mtime_ms: number | null = null;
        try {
          const st = await fs.stat(childAbs);
          size_bytes = isDir ? null : st.size;
          mtime_ms = Math.trunc(st.mtimeMs);
        } catch {
          /* leave nulls */
        }
        results.push({
          path: toPosix(relative(root.absPath, childAbs)),
          is_dir: isDir,
          size_bytes,
          mtime_ms,
        });
      }
      if (recursive && isDir && depth + 1 <= LIST_FILES_MAX_DEPTH) {
        await walk(join(absDir, d.name), childRel, depth + 1);
      }
    }
  };

  await walk(baseReal.path, "", 0);
  results.sort((a, b) => a.path.localeCompare(b.path));
  return opOk({
    entries: results.slice(0, limit),
    truncated: results.length > limit,
  });
}

function splitLinesLikePython(text: string): string[] {
  if (text === "") return [];
  const normalized = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = normalized.split("\n");
  if (normalized.endsWith("\n") && lines.length > 0) {
    lines.pop();
  }
  return lines;
}

export async function opReadLines(
  root: StoredRoot,
  relPath: string,
  offset: number,
  limit: number | null,
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
  if (st.size > WORKSPACE_READ_MAX)
    return opErr("WorkspaceIOError", "文件过大，无法读取");
  const real = await realInside(root, abs);
  if (!real.ok) {
    return real.code === "out_of_root"
      ? opErr("OutsideWorkspace", relPath)
      : opErr("PathNotFound", relPath);
  }
  try {
    const buf = await fs.readFile(real.path);
    if (buf.includes(0))
      return opErr("WorkspaceIOError", "二进制文件，无法以文本读取");
    const lines = splitLinesLikePython(buf.toString("utf-8"));
    const total = lines.length;
    const startIdx = Math.max(0, offset - 1);
    if (startIdx >= total) {
      return opOk({
        lines: [],
        start_line: offset,
        end_line: offset - 1,
        total_lines: total,
      });
    }
    const endIdx =
      limit == null ? total : Math.min(total, startIdx + Math.max(0, limit));
    const selected = lines.slice(startIdx, endIdx);
    return opOk({
      lines: selected,
      start_line: startIdx + 1,
      end_line: endIdx,
      total_lines: total,
    });
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
}

export async function opListTree(
  root: StoredRoot,
  directory: string,
  pattern: string,
  maxDepth: number,
  maxEntries: number,
  revealPaths?: ReadonlySet<string>,
  listOptions?: AiListSkipOptions,
): Promise<WorkspaceOpResult> {
  const baseAbs = resolveLexical(root, directory);
  if (!baseAbs) return opErr("OutsideWorkspace", directory);
  const baseReal = await realInside(root, baseAbs);
  // 裸聊懒建尚未 mkdir 时 list_tree 与 index_files 同口径：子树不存在 → 空列表；
  // 路径存在但非目录 → NotADirectory；越界仍硬错。
  if (!baseReal.ok) {
    if (baseReal.code === "out_of_root") {
      return opErr("OutsideWorkspace", directory);
    }
    return opOk({
      entries: [],
      truncated: false,
      elided_count: 0,
      warnings: [],
    });
  }
  let baseStat: import("node:fs").Stats | undefined;
  try {
    baseStat = await fs.stat(baseReal.path);
  } catch {
    baseStat = undefined;
  }
  if (!baseStat?.isDirectory()) {
    return opErr("NotADirectory", directory);
  }

  const entries: { path: string; is_dir: boolean; depth: number }[] = [];
  let truncated = false;
  let elidedCount = 0;
  const warnings: string[] = [];
  const nameFilter = pattern || "*";
  const nameSearch = nameFilter !== "*";
  const nameMatches = (name: string) => globToRegExp(nameFilter).test(name);

  const walk = async (
    absDir: string,
    parentRel: string,
    depth: number,
    isRoot: boolean,
  ): Promise<void> => {
    if (depth > maxDepth) return;
    let dirents: import("node:fs").Dirent[];
    try {
      dirents = await fs.readdir(absDir, { withFileTypes: true });
    } catch (e) {
      if (!isRoot && isAccessDeniedError(e)) {
        warnings.push(`跳过无权限目录：${parentRel || "."}`);
        return;
      }
      throw e;
    }
    dirents.sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    );
    for (const d of dirents) {
      // Name-first ignore prune before descending into locked noise dirs.
      // AI list_tree: attachments/ + reveal_paths + external/archives exemptions.
      if (
        shouldSkipAiListEntry(d.name, true, parentRel, revealPaths, listOptions)
      )
        continue;
      const isDir = d.isDirectory() && !d.isSymbolicLink();
      if (
        !isDir &&
        shouldSkipAiListEntry(
          d.name,
          false,
          parentRel,
          revealPaths,
          listOptions,
        )
      )
        continue;
      const childAbs = join(absDir, d.name);
      const childRel = parentRel ? `${parentRel}/${d.name}` : d.name;
      // `*`: emit dirs + matching files. Name filter: emit matches only;
      // still descend unmatched dirs so max_entries is spent on hits.
      const emit = nameSearch
        ? nameMatches(d.name)
        : isDir || nameMatches(d.name);
      if (emit) {
        if (entries.length >= maxEntries) {
          truncated = true;
          elidedCount += 1;
          continue;
        }
        entries.push({
          path: toPosix(relative(root.absPath, childAbs)),
          is_dir: isDir,
          depth,
        });
      }
      if (isDir && depth < maxDepth) {
        await walk(childAbs, childRel, depth + 1, false);
      }
    }
  };

  try {
    const baseRel = toPosix(relative(root.absPath, baseReal.path));
    await walk(baseReal.path, baseRel === "." ? "" : baseRel, 1, true);
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  return opOk({
    entries,
    truncated,
    elided_count: elidedCount,
    warnings,
  });
}

// index_files：把绑定根（或其 `base` 子树）扁平索引成相对文件路径列表（忽略目录剪枝 + cap），
// 返回 {entries, paths, truncated}。`entries` 带本机指纹（mtime_ms / size_bytes，stat 非 hash），
// 供服务端跳过未变文件整文过桥；`paths` = entries.path 列表，兼容旧读法。
// 服务端 LocalWorkspace.index_files 经此打通，使 @ 提及与 worker 工作区清单在本地根上与云端
// ServerWorkspace.index_files 行为一致。`order` 选排序（"recent" 按 mtime 倒序供清单预算，否则字母序）。
//
// `base` = 工作区子路径（工作区对称化 D1a）：把索引限定到该子树，并把子路径前缀**拼回**各结果
// （故返回的是 root-相对路径），服务端 `LocalWorkspace._out` 再剥成工作区相对——与 list/grep
// 回填 root-相对、服务端统一剥前缀的约定一致。`""` / `"."` = 整根（现行为，无前缀）。子树尚不
// 存在（裸聊懒建后尚未产文件）→ 空列表。
export async function opIndexFiles(
  root: StoredRoot,
  order: "path" | "recent",
  base = "",
): Promise<WorkspaceOpResult> {
  const sub = base === "." ? "" : base.replace(/^\/+|\/+$/g, "");
  const baseAbs = resolveLexical(root, sub || ".");
  if (!baseAbs) return opErr("OutsideWorkspace", base);
  const baseReal = await realInside(root, baseAbs);
  // 子树尚不存在（裸聊懒建后尚未产文件）→ 空列表；越界仍硬错。
  if (!baseReal.ok) {
    if (baseReal.code === "out_of_root") {
      return opErr("OutsideWorkspace", base);
    }
    return opOk({ entries: [], paths: [], truncated: false });
  }
  const { files, truncated } = await collectWorkspaceFiles(
    baseReal.path,
    order,
    { fingerprint: true },
  );
  const prefix = sub ? `${sub}/` : "";
  const entries = files.map((f) => ({
    path: prefix + f.relPath,
    mtime_ms: Math.trunc(f.mtimeMs),
    size_bytes: f.sizeBytes,
  }));
  return opOk({
    entries,
    paths: entries.map((e) => e.path),
    truncated,
  });
}
