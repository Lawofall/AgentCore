/**
 * 产品 AI grep / glob_files：桌面通道侧走内嵌 ripgrep。
 *
 * 一次 rg：流式读到结果帽即停。不预列举、不按 argv 分块、不 ``--sort path``。
 * 忽略 = 产品名集（``--no-ignore``）。grep glob 仅文件名（normalize 后 ``--glob``）。
 * 返回集再排序；截断 = 本批触顶。缺二进制 = 显式 WorkspaceIOError。
 */
import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import { dirname, join, relative } from "node:path";
import type { WorkspaceOpResult } from "@shared/ipc-contract";
import { GREP_MAX_FILE_BYTES, GREP_MAX_RESULTS_CAP } from "../constants";
import { realInside, resolveLexical, toReason } from "../pathGuard";
import type { StoredRoot } from "../roots";
import {
  AI_ARCHIVE_FILE_SUFFIXES,
  AI_IMAGE_FILE_SUFFIXES,
  AI_NOISE_FILE_SUFFIXES,
  BASELINES_REL,
  INDEX_REL,
  LIST_FILES_SKIP_DIRS,
  SYSTEM_IGNORED_FILE_SUFFIXES,
  TRASH_REL,
  VERSIONS_REL,
} from "../workspaceIgnore";
import { opErr, opOk, toPosix, trimLine } from "./result";
import { resolveRgBinary } from "./rgBinary";

type IgnoreKind = "grep" | "list";

const RG_MISSING =
  "ripgrep 二进制未找到（未设置 AGENTCORE_RG_PATH / 未内嵌 rg）。";

/** 单次 rg 子进程墙钟；满 N 会先杀。外层工具活性仍是 60s。 */
export const RG_CHILD_TIMEOUT_MS = 50_000;

/** 与服务端 `normalize_glob` 对齐：只保留文件名段。 */
export function normalizeGlob(globPat: string): string | null {
  let p = globPat.trim().replace(/\\/g, "/");
  if (!p) return null;
  if (p.startsWith("**/")) p = p.slice(3);
  if (p.includes("/")) p = p.slice(p.lastIndexOf("/") + 1);
  return p || null;
}

function productIgnoreGlobs(
  kind: IgnoreKind,
  revealArchives: boolean,
): string[] {
  const globs: string[] = [];
  for (const name of [...LIST_FILES_SKIP_DIRS].sort()) {
    globs.push(`!${name}`, `!**/${name}/**`);
  }
  for (const zone of [INDEX_REL, TRASH_REL, BASELINES_REL, VERSIONS_REL]) {
    globs.push(`!${zone}`, `!${zone}/**`);
  }
  const suffixes = new Set<string>(SYSTEM_IGNORED_FILE_SUFFIXES);
  const images = new Set<string>(AI_IMAGE_FILE_SUFFIXES);
  const archives = new Set<string>(AI_ARCHIVE_FILE_SUFFIXES);
  if (kind === "grep") {
    for (const s of AI_NOISE_FILE_SUFFIXES) suffixes.add(s);
  } else {
    for (const s of AI_NOISE_FILE_SUFFIXES) {
      if (!images.has(s)) suffixes.add(s);
    }
    if (revealArchives) {
      for (const s of archives) suffixes.delete(s);
    }
  }
  for (const suf of [...suffixes].sort()) {
    globs.push(`!**/*${suf}`);
  }
  return globs;
}

function commonRgFlags(opts: {
  caseInsensitive?: boolean;
  nameGlobs?: string[];
  kind?: IgnoreKind;
  revealArchives?: boolean;
  maxDepth?: number | null;
}): string[] {
  const args = [
    "--no-ignore",
    "--no-config",
    "--hidden",
    "--line-buffered",
    "--color",
    "never",
    "--max-filesize",
    String(GREP_MAX_FILE_BYTES),
  ];
  if (opts.caseInsensitive) args.push("--ignore-case");
  if (opts.maxDepth != null && Number.isFinite(opts.maxDepth)) {
    args.push("--max-depth", String(Math.max(0, Math.trunc(opts.maxDepth))));
  }
  for (const g of productIgnoreGlobs(
    opts.kind ?? "grep",
    Boolean(opts.revealArchives),
  )) {
    args.push("--glob", g);
  }
  for (const g of opts.nameGlobs ?? []) {
    if (g) args.push("--glob", g);
  }
  return args;
}

function runRgCapped(
  rg: string,
  args: string[],
  cwd: string,
  maxLines: number,
  timeoutMs: number = RG_CHILD_TIMEOUT_MS,
): Promise<{
  code: number;
  lines: string[];
  stderr: string;
  truncated: boolean;
}> {
  return new Promise((resolve, reject) => {
    const child = spawn(rg, args, {
      cwd,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const lines: string[] = [];
    let stderr = "";
    let stdoutBuf = "";
    let truncated = false;
    let settled = false;
    let timedOut = false;
    const timer =
      timeoutMs > 0
        ? setTimeout(() => {
            timedOut = true;
            try {
              child.kill("SIGKILL");
            } catch {
              // process already gone
            }
          }, timeoutMs)
        : undefined;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      if (timer != null) clearTimeout(timer);
      fn();
    };
    const killChild = () => {
      try {
        child.kill("SIGKILL");
      } catch {
        // process already gone
      }
    };
    const takeLine = (line: string) => {
      if (truncated || !line) return;
      lines.push(line);
      if (lines.length > maxLines) {
        truncated = true;
        killChild();
      }
    };
    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      if (truncated) return;
      stdoutBuf += chunk;
      for (;;) {
        const nl = stdoutBuf.search(/\r?\n/);
        if (nl < 0) break;
        const line = stdoutBuf.slice(0, nl);
        stdoutBuf = stdoutBuf.slice(nl).replace(/^\r?\n/, "");
        takeLine(line);
        if (truncated) return;
      }
    });
    child.stderr?.on("data", (c: string) => {
      stderr += c;
    });
    child.on("error", (err) => {
      finish(() => reject(err));
    });
    child.on("close", (code) => {
      finish(() => {
        if (timedOut) {
          reject(
            new Error(
              `ripgrep 超时（>${timeoutMs}ms）——已终止子进程，请收窄 path/glob 或简化 pattern`,
            ),
          );
          return;
        }
        if (!truncated && stdoutBuf) {
          takeLine(stdoutBuf.replace(/\r$/, ""));
          stdoutBuf = "";
        }
        const kept = truncated ? lines.slice(0, maxLines) : lines;
        resolve({
          code: truncated ? 0 : (code ?? 2),
          lines: kept,
          stderr,
          truncated,
        });
      });
    });
  });
}

const RG_CLI_FLAG_LINE =
  /consider enabling|--pcre2|--multiline|\(or -U for short\)|when multiline mode is enabled/i;
const LOOKAROUND_STEER =
  "本工具是 Rust regex，不支持 lookahead/lookbehind。请改成不含 (?!)/(?=)/(?< 的简单模式，或拆成多次 grep。";
const NEWLINE_STEER =
  "不要把字面换行或 \\n 写进正则。跨行请拆成多次搜索，或只搜其中一行。";

function regexDiagnosticDetail(stderr: string): string {
  const lines = stderr
    .split(/\r?\n/)
    .map((l) => l.replace(/[ \t]+$/g, ""))
    .filter((l) => l.trim().length > 0 && !RG_CLI_FLAG_LINE.test(l));
  return lines.length > 0 ? lines.join("\n") : "";
}

function regexErrorMessage(stderr: string): string | null {
  const text = stderr.trim();
  if (!text) return null;
  const lower = text.toLowerCase();
  if (
    !lower.includes("regex") &&
    !lower.includes("parse error") &&
    !lower.includes("syntax error")
  ) {
    return null;
  }
  const detail = regexDiagnosticDetail(text) || "正则语法无法解析";
  let msg = `正则表达式无效：${detail}`;
  if (
    lower.includes("look-around") ||
    lower.includes("look-ahead") ||
    lower.includes("look-behind")
  ) {
    msg = `${msg}\n${LOOKAROUND_STEER}`;
  }
  if (lower.includes("literal") && text.includes("\\n")) {
    msg = `${msg}\n${NEWLINE_STEER}`;
  }
  return msg;
}

function handleRgStatus(code: number, stderr: string): string[] {
  if (code === 0 || code === 1) return [];
  const regexMsg = regexErrorMessage(stderr);
  if (regexMsg) throw new Error(regexMsg);
  const ioWarnings = rgIoWarnings(stderr);
  if (ioWarnings !== null) return ioWarnings;
  const detail = stderr.trim() || `rg exited with code ${code}`;
  throw new Error(`ripgrep 失败：${detail}`);
}

const RG_IO_HINTS = [
  "permission denied",
  "access is denied",
  "access denied",
  "os error 5",
  "os error 13",
  "os error 32",
  "拒绝访问",
];

function isRgIoLine(line: string): boolean {
  const lower = line.toLowerCase();
  return RG_IO_HINTS.some((h) => lower.includes(h));
}

/** Soft-skip warnings when stderr is solely per-path IO denials; else null. */
function rgIoWarnings(stderr: string): string[] | null {
  const lines = stderr
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length === 0) return null;
  if (lines.some((ln) => !isRgIoLine(ln))) return null;
  return lines.map((ln) => {
    const body = ln.toLowerCase().startsWith("rg:") ? ln.slice(3).trim() : ln;
    return `跳过无权限路径：${body}`;
  });
}

function parseLineHit(
  line: string,
): { path: string; lineNo: number; text: string } | null {
  const m = /^(.*):(\d+):(.*)$/.exec(line);
  if (!m) return null;
  return { path: m[1], lineNo: Number(m[2]), text: m[3] };
}

function toRel(
  rawPath: string,
  opts: {
    searchCwd: string;
    rootAbs: string;
    singleFile: boolean;
    fileAbs: string;
  },
): string {
  if (opts.singleFile) return toPosix(relative(opts.rootAbs, opts.fileAbs));
  const abs = rawPath.match(/^[A-Za-z]:[\\/]/)
    ? rawPath
    : join(opts.searchCwd, rawPath);
  return toPosix(relative(opts.rootAbs, abs));
}

export async function opGrep(
  root: StoredRoot,
  args: Record<string, unknown>,
): Promise<WorkspaceOpResult> {
  const pattern = String(args.pattern ?? "");
  const directory = String(args.directory ?? ".");
  const glob = args.glob ? String(args.glob) : "";
  const caseInsensitive = Boolean(args.case_insensitive);
  const filesOnly = Boolean(args.files_only);
  const maxResults = Math.max(
    1,
    Math.min(Number(args.max_results ?? 50), GREP_MAX_RESULTS_CAP),
  );

  const baseAbs = resolveLexical(root, directory);
  if (!baseAbs) return opErr("OutsideWorkspace", directory);
  const baseReal = await realInside(root, baseAbs);
  if (!baseReal.ok) {
    return baseReal.code === "out_of_root"
      ? opErr("OutsideWorkspace", directory)
      : opErr("PathNotFound", directory);
  }

  let baseIsFile = false;
  try {
    const st = await fs.stat(baseReal.path);
    baseIsFile = st.isFile();
    if (!st.isDirectory() && !st.isFile()) {
      return opErr("PathNotFound", directory);
    }
  } catch {
    return opErr("PathNotFound", directory);
  }

  const rg = resolveRgBinary();
  if (!rg) return opErr("WorkspaceIOError", RG_MISSING);

  const nameGlob = baseIsFile ? null : normalizeGlob(glob);
  const searchCwd = baseIsFile ? dirname(baseReal.path) : baseReal.path;
  const target = baseIsFile ? baseReal.path : ".";
  const modeFlags = filesOnly
    ? ["--files-with-matches"]
    : ["--line-number", "--with-filename", "--no-heading"];

  try {
    const ran = await runRgCapped(
      rg,
      [
        ...modeFlags,
        ...commonRgFlags({
          caseInsensitive,
          nameGlobs: nameGlob ? [nameGlob] : [],
          kind: "grep",
        }),
        "--regexp",
        pattern,
        "--",
        target,
      ],
      searchCwd,
      maxResults,
    );
    const softWarnings = ran.truncated
      ? (rgIoWarnings(ran.stderr) ?? [])
      : handleRgStatus(ran.code, ran.stderr);
    const relOpts = {
      searchCwd,
      rootAbs: root.absPath,
      singleFile: baseIsFile,
      fileAbs: baseReal.path,
    };

    if (filesOnly) {
      const seen = new Set<string>();
      const rels: string[] = [];
      for (const ln of ran.lines) {
        const rel = toRel(ln.replace(/\\/g, "/"), relOpts);
        if (seen.has(rel)) continue;
        seen.add(rel);
        rels.push(rel);
      }
      rels.sort((a, b) => a.localeCompare(b));
      let truncated = ran.truncated;
      let fileCounts = rels.map((rel) => [rel, 1] as [string, number]);
      if (fileCounts.length > maxResults) {
        truncated = true;
        fileCounts = fileCounts.slice(0, maxResults);
      }
      return opOk({
        hits: [],
        file_counts: fileCounts,
        total_matches: fileCounts.length,
        truncated,
        warnings: softWarnings,
      });
    }

    const parsedHits = ran.lines
      .map(parseLineHit)
      .filter((x): x is NonNullable<typeof x> => x !== null)
      .map((x) => ({
        path: toRel(x.path, relOpts),
        line_no: x.lineNo,
        text: trimLine(x.text),
      }))
      .sort((a, b) => a.path.localeCompare(b.path) || a.line_no - b.line_no);

    let truncated = ran.truncated;
    let hits = parsedHits;
    if (hits.length > maxResults) {
      truncated = true;
      hits = hits.slice(0, maxResults);
    }
    const fileCountsMap = new Map<string, number>();
    for (const h of hits) {
      fileCountsMap.set(h.path, (fileCountsMap.get(h.path) ?? 0) + 1);
    }
    return opOk({
      hits,
      file_counts: [...fileCountsMap.entries()].sort((a, b) =>
        a[0].localeCompare(b[0]),
      ),
      total_matches: hits.length,
      truncated,
      warnings: softWarnings,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.startsWith("正则表达式无效")) {
      return opErr("WorkspaceIOError", msg);
    }
    return opErr("WorkspaceIOError", toReason(e));
  }
}

export async function opGlobFiles(
  root: StoredRoot,
  args: Record<string, unknown>,
): Promise<WorkspaceOpResult> {
  const directory = String(args.directory ?? ".");
  const globs = Array.isArray(args.globs)
    ? args.globs.map((g) => String(g)).filter(Boolean)
    : [];
  const maxDepth =
    args.max_depth == null || args.max_depth === ""
      ? null
      : Number(args.max_depth);
  const maxEntries = Math.max(
    1,
    Math.min(Number(args.max_entries ?? 50), GREP_MAX_RESULTS_CAP),
  );
  const revealArchives = Boolean(args.reveal_archives);

  const baseAbs = resolveLexical(root, directory);
  if (!baseAbs) return opErr("OutsideWorkspace", directory);
  const baseReal = await realInside(root, baseAbs);
  if (!baseReal.ok) {
    return baseReal.code === "out_of_root"
      ? opErr("OutsideWorkspace", directory)
      : opErr("PathNotFound", directory);
  }

  try {
    const st = await fs.stat(baseReal.path);
    if (!st.isDirectory()) return opErr("NotADirectory", directory);
  } catch {
    return opErr("PathNotFound", directory);
  }

  const rg = resolveRgBinary();
  if (!rg) return opErr("WorkspaceIOError", RG_MISSING);

  try {
    const ran = await runRgCapped(
      rg,
      [
        "--files",
        ...commonRgFlags({
          nameGlobs: globs,
          kind: "list",
          revealArchives,
          maxDepth: Number.isFinite(maxDepth as number) ? maxDepth : null,
        }),
        "--",
        ".",
      ],
      baseReal.path,
      maxEntries,
    );
    const warnings = ran.truncated
      ? (rgIoWarnings(ran.stderr) ?? [])
      : handleRgStatus(ran.code, ran.stderr);
    const seen = new Set<string>();
    const paths: string[] = [];
    for (const ln of ran.lines) {
      const raw = ln.replace(/\\/g, "/").trim();
      if (!raw) continue;
      const rel = toRel(raw, {
        searchCwd: baseReal.path,
        rootAbs: root.absPath,
        singleFile: false,
        fileAbs: baseReal.path,
      });
      if (seen.has(rel)) continue;
      seen.add(rel);
      paths.push(rel);
    }
    paths.sort((a, b) => a.localeCompare(b));
    let truncated = ran.truncated;
    let out = paths;
    if (out.length > maxEntries) {
      truncated = true;
      out = out.slice(0, maxEntries);
    }
    return opOk({ paths: out, truncated, warnings });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.startsWith("正则表达式无效")) {
      return opErr("WorkspaceIOError", msg);
    }
    return opErr("WorkspaceIOError", toReason(e));
  }
}
