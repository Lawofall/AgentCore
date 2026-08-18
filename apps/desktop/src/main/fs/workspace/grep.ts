/**
 * 产品 AI grep：桌面通道侧（LocalWorkspace → opGrep）走内嵌 ripgrep。
 *
 * 语义与服务端 `workspace/rg_grep.py` 对齐：先 pathGuard，再 rg；单文件忽略 glob；
 * glob 仅文件名（normalize 后 `--glob`）；`--no-ignore` + 产品名集；截断前按 path/line
 * 稳定排序；文件大小帽 2MiB。缺二进制 = 显式 WorkspaceIOError，禁止回退 JS walk。
 */
import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import type { WorkspaceOpResult } from "@shared/ipc-contract";
import {
  GREP_MAX_FILES,
  GREP_MAX_FILE_BYTES,
  GREP_MAX_RESULTS_CAP,
} from "../constants";
import { realInside, resolveLexical, toReason } from "../pathGuard";
import type { StoredRoot } from "../roots";
import {
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

const FILE_ARG_CHUNK = 200;

/** 单次 rg 子进程墙钟（目录列举/批量搜）；超时 SIGKILL，避免拖到通道 ~60s。 */
export const RG_CHILD_TIMEOUT_MS = 45_000;
/** 单文件根（``rg PATTERN FILE``）应毫秒级完成；更短墙钟便于早回传业务错。 */
export const RG_SINGLE_FILE_TIMEOUT_MS = 10_000;

/** 与服务端 `normalize_glob` 对齐：只保留文件名段。 */
export function normalizeGlob(globPat: string): string | null {
  let p = globPat.trim().replace(/\\/g, "/");
  if (!p) return null;
  if (p.startsWith("**/")) p = p.slice(3);
  if (p.includes("/")) p = p.slice(p.lastIndexOf("/") + 1);
  return p || null;
}

function productIgnoreGlobs(): string[] {
  const globs: string[] = [];
  for (const name of [...LIST_FILES_SKIP_DIRS].sort()) {
    globs.push(`!${name}`, `!**/${name}/**`);
  }
  for (const zone of [INDEX_REL, TRASH_REL, BASELINES_REL, VERSIONS_REL]) {
    globs.push(`!${zone}`, `!${zone}/**`);
  }
  const suffixes = [
    ...SYSTEM_IGNORED_FILE_SUFFIXES,
    ...AI_NOISE_FILE_SUFFIXES,
  ].sort();
  for (const suf of suffixes) {
    globs.push(`!**/*${suf}`);
  }
  return globs;
}

function commonRgFlags(opts: {
  caseInsensitive: boolean;
  nameGlob: string | null;
  applyProductIgnore: boolean;
}): string[] {
  const args = [
    "--no-ignore",
    "--no-config",
    "--hidden",
    "--color",
    "never",
    "--max-filesize",
    String(GREP_MAX_FILE_BYTES),
    "--sort",
    "path",
  ];
  if (opts.caseInsensitive) args.push("--ignore-case");
  if (opts.applyProductIgnore) {
    for (const g of productIgnoreGlobs()) {
      args.push("--glob", g);
    }
  }
  if (opts.nameGlob) args.push("--glob", opts.nameGlob);
  return args;
}

function runRg(
  rg: string,
  args: string[],
  cwd: string,
  timeoutMs: number = RG_CHILD_TIMEOUT_MS,
): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(rg, args, {
      cwd,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
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
    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (c: string) => {
      stdout += c;
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
        resolve({ code: code ?? 2, stdout, stderr });
      });
    });
  });
}

function regexDiagnosticDetail(stderr: string): string {
  // Keep rg's useful diagnostic, not a header-only first line.
  // ``rg: regex parse error:`` is a label; the reason lives on later lines.
  const lines = stderr
    .split(/\r?\n/)
    .map((l) => l.replace(/[ \t]+$/g, ""))
    .filter((l) => l.trim().length > 0);
  return lines.length > 0 ? lines.join("\n") : stderr.trim();
}

function regexErrorMessage(stderr: string): string | null {
  const text = stderr.trim();
  if (!text) return null;
  const lower = text.toLowerCase();
  if (
    lower.includes("regex") ||
    lower.includes("parse error") ||
    lower.includes("syntax error")
  ) {
    return `正则表达式无效：${regexDiagnosticDetail(text)}`;
  }
  return null;
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

function parseCountLine(line: string): { path: string; count: number } | null {
  const m = /^(.*):(\d+)$/.exec(line);
  if (!m) return null;
  return { path: m[1], count: Number(m[2]) };
}

/** Unique Temp dir + empty file for ``rg --regexp`` parse probe (exported for tests). */
export async function allocateRgRegexpProbe(): Promise<{
  probeDir: string;
  probePath: string;
  cleanup: () => Promise<void>;
}> {
  // mkdtemp (not pid+Date.now): concurrent opGrep in one Electron main must not
  // share a probe path — sibling unlink → rg os error 2 (see dogfood 070ce1).
  const probeDir = await fs.mkdtemp(join(tmpdir(), "agentcore-rg-probe-"));
  const probePath = join(probeDir, "probe.txt");
  await fs.writeFile(probePath, "", "utf8");
  return {
    probeDir,
    probePath,
    cleanup: async () => {
      await fs
        .rm(probeDir, { recursive: true, force: true })
        .catch(() => undefined);
    },
  };
}

async function validateRegexp(rg: string, pattern: string): Promise<void> {
  const { probeDir, probePath, cleanup } = await allocateRgRegexpProbe();
  try {
    const ran = await runRg(
      rg,
      ["--regexp", pattern, "--", probePath],
      probeDir,
    );
    handleRgStatus(ran.code, ran.stderr);
  } finally {
    await cleanup();
  }
}

async function searchPaths(
  rg: string,
  opts: {
    pattern: string;
    paths: string[];
    cwd: string;
    caseInsensitive: boolean;
    filesOnly: boolean;
    timeoutMs?: number;
  },
): Promise<{ stdout: string; warnings: string[] }> {
  if (opts.paths.length === 0) return { stdout: "", warnings: [] };
  const modeFlags = opts.filesOnly
    ? ["--count", "--with-filename"]
    : ["--line-number", "--with-filename", "--no-heading"];
  const base = [
    ...modeFlags,
    ...commonRgFlags({
      caseInsensitive: opts.caseInsensitive,
      nameGlob: null,
      applyProductIgnore: false,
    }),
    "--regexp",
    opts.pattern,
  ];
  const childTimeout = opts.timeoutMs ?? RG_CHILD_TIMEOUT_MS;
  const chunks: string[] = [];
  const warnings: string[] = [];
  for (let i = 0; i < opts.paths.length; i += FILE_ARG_CHUNK) {
    const chunk = opts.paths.slice(i, i + FILE_ARG_CHUNK);
    const ran = await runRg(
      rg,
      [...base, "--", ...chunk],
      opts.cwd,
      childTimeout,
    );
    warnings.push(...handleRgStatus(ran.code, ran.stderr));
    if (ran.stdout) chunks.push(ran.stdout);
  }
  return { stdout: chunks.join(""), warnings };
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
  if (!rg) {
    return opErr(
      "WorkspaceIOError",
      "ripgrep 二进制未找到（未设置 AGENTCORE_RG_PATH / 未内嵌 rg）。请运行: python apps/server/scripts/fetch_ripgrep.py --install-desktop",
    );
  }

  const nameGlob = baseIsFile ? null : normalizeGlob(glob);
  // 单文件：cwd=父目录，path 传绝对路径（勿仅靠 basename——并发/怪异 cwd 下更稳）。
  const searchCwd = baseIsFile ? dirname(baseReal.path) : baseReal.path;
  const toRel = (absFile: string) => toPosix(relative(root.absPath, absFile));

  try {
    let candidateFiles: string[] = [];
    let scanTruncated = false;
    const softWarnings: string[] = [];

    if (baseIsFile) {
      // 单文件根：跳过 ``--files`` 列举与独立 regexp probe（搜该文件时 rg 会验正则）。
      candidateFiles = [baseReal.path];
    } else {
      // 空候选时仍需 probe，否则坏正则会与「无匹配」混淆。
      await validateRegexp(rg, pattern);
      const listArgs = [
        "--files",
        ...commonRgFlags({
          caseInsensitive,
          nameGlob,
          applyProductIgnore: true,
        }),
        ".",
      ];
      const listed = await runRg(rg, listArgs, searchCwd, RG_CHILD_TIMEOUT_MS);
      softWarnings.push(...handleRgStatus(listed.code, listed.stderr));
      candidateFiles = listed.stdout
        .split(/\r?\n/)
        .map((l) => l.replace(/\\/g, "/").trim())
        .filter(Boolean)
        .sort();
      if (candidateFiles.length > GREP_MAX_FILES) {
        scanTruncated = true;
        candidateFiles = candidateFiles.slice(0, GREP_MAX_FILES);
      }
      if (candidateFiles.length === 0) {
        return opOk({
          hits: [],
          file_counts: [],
          total_matches: 0,
          truncated: scanTruncated,
          warnings: softWarnings,
        });
      }
    }

    const searched = await searchPaths(rg, {
      pattern,
      paths: candidateFiles,
      cwd: searchCwd,
      caseInsensitive,
      filesOnly,
      timeoutMs: baseIsFile ? RG_SINGLE_FILE_TIMEOUT_MS : RG_CHILD_TIMEOUT_MS,
    });
    softWarnings.push(...searched.warnings);
    const lines = searched.stdout.split(/\r?\n/).filter(Boolean);

    if (filesOnly) {
      const parsed = lines
        .map(parseCountLine)
        .filter((x): x is NonNullable<typeof x> => x !== null)
        .map((x) => {
          // 单文件：rg 可能回绝对路径或 basename；命中路径恒为该文件。
          const abs = baseIsFile ? baseReal.path : join(searchCwd, x.path);
          return [toRel(abs), x.count] as [string, number];
        })
        .sort((a, b) => a[0].localeCompare(b[0]));
      let truncated = scanTruncated;
      let fileCounts = parsed;
      if (fileCounts.length > maxResults) {
        truncated = true;
        fileCounts = fileCounts.slice(0, maxResults);
      }
      const total = fileCounts.reduce((s, [, c]) => s + c, 0);
      return opOk({
        hits: [],
        file_counts: fileCounts,
        total_matches: total,
        truncated,
        warnings: softWarnings,
      });
    }

    const parsedHits = lines
      .map(parseLineHit)
      .filter((x): x is NonNullable<typeof x> => x !== null)
      .map((x) => {
        const abs = baseIsFile ? baseReal.path : join(searchCwd, x.path);
        return {
          path: toRel(abs),
          line_no: x.lineNo,
          text: trimLine(x.text),
        };
      })
      .sort((a, b) => a.path.localeCompare(b.path) || a.line_no - b.line_no);

    let truncated = scanTruncated;
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
