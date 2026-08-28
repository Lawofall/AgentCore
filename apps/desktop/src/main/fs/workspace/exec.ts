import { spawn } from "node:child_process";
import { promises as fs, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import type { WorkspaceOpResult } from "@shared/ipc-contract";
import { killProcessTree, treeSpawnOptions } from "../../proc-tree";
import { EXEC_CAPTURE_CAP, EXEC_LANGS, EXEC_TIMEOUT_CAP_S } from "../constants";
import { realInside, resolveLexical, toReason } from "../pathGuard";
import type { StoredRoot } from "../roots";
import { getRoot } from "../roots";
import {
  decodePipeChunk,
  isSpawnDeniedError,
  launcherMissingStderr,
  resolveBashLauncher,
  spawnDeniedStderr,
  whichCommand,
} from "./execCodec";
import { opErr, opOk } from "./result";
import { withWrittenFiles, writtenScanCutoffMs } from "./writtenScan";

/** ExecutionResult 形状的成功信封（success 可为 false——执行「跑完了但非 0 退出」）。 */
function execResult(value: {
  success: boolean;
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
}): WorkspaceOpResult {
  return opOk(value);
}

/** Layout dirs auto-prepended to PYTHONPATH (mirrors server ``default_pythonpath_rels``). */
const PYTHONPATH_AUTO_EXTRA = ["src", "lib"] as const;

/**
 * D11′: workspace ``.`` + existing ``src``/``lib`` for local ``code_execute``,
 * same resolution as server ``merge_pythonpath_into_env`` (auto mode).
 */
export function buildWorkspacePythonpathEnv(
  cwdAbs: string,
  prevPythonpath?: string,
): Record<string, string> {
  const entries: string[] = [cwdAbs];
  for (const name of PYTHONPATH_AUTO_EXTRA) {
    const p = join(cwdAbs, name);
    if (existsSync(p)) entries.push(p);
  }
  const prev = prevPythonpath ?? process.env.PYTHONPATH ?? "";
  const merged = [...entries, prev].filter(Boolean).join(delimiter);
  return { PYTHONPATH: merged };
}

/**
 * W3: build ``AGENTCORE_EXTERNAL_<ALIAS>`` env map from ``external_roots``.
 * Only injects roots that are sessionOnly grants bound to ``conversationId``.
 */
export function buildExternalEnvFromRoots(
  externalRoots: Record<string, unknown> | null | undefined,
  conversationId: string,
  lookup: (rootId: string) => StoredRoot | undefined = getRoot,
): Record<string, string> {
  const envExtra: Record<string, string> = {};
  if (!externalRoots || typeof externalRoots !== "object" || !conversationId) {
    return envExtra;
  }
  for (const [alias, rootId] of Object.entries(externalRoots)) {
    const rid = String(rootId ?? "");
    const er = rid ? lookup(rid) : undefined;
    if (
      !er?.absPath ||
      !er.sessionOnly ||
      er.conversationId !== conversationId
    ) {
      continue;
    }
    // Organize mounts must NOT inject AGENTCORE_EXTERNAL_* (proposal §五).
    if (er.mode === "organize") continue;
    const safe =
      alias
        .replace(/[^A-Za-z0-9]+/g, "_")
        .replace(/^_|_$/g, "")
        .toUpperCase() || "FOLDER";
    envExtra[`AGENTCORE_EXTERNAL_${safe}`] = er.absPath;
  }
  return envExtra;
}

/**
 * Resolve argv for ``language`` (absolute bash when needed). Returns ``null`` +
 * stderr when the launcher is missing / is a rejected WSL trampoline — fail-fast,
 * never hang on a broken System32\\bash.exe.
 *
 * python/node keep bare names so Windows PATHEXT / ``.cmd`` shims still work
 * via CreateProcess; only bash is absolutized.
 */
function resolveLangCmd(
  language: string,
  lang: { cmd: string[]; ext: string },
): { cmd: string[] } | { error: string } {
  if (language === "bash") {
    const bash = resolveBashLauncher();
    if (!bash) {
      return { error: launcherMissingStderr("bash", "bash") };
    }
    return { cmd: [bash] };
  }
  const binName = lang.cmd[0];
  if (!whichCommand(binName)) {
    return { error: launcherMissingStderr(binName, language) };
  }
  return { cmd: [...lang.cmd] };
}

/**
 * Allowlist registry / package-cache env keys from the server execute payload.
 * Arbitrary env injection from the API is rejected (PATH / LD_* / secrets…).
 */
const REGISTRY_ENV_KEY =
  /^(NPM_CONFIG_|npm_config_|YARN_|PNPM_|PIP_|UV_|POETRY_|HTTPS_PROXY|HTTP_PROXY|ALL_PROXY|NO_PROXY|https_proxy|http_proxy|all_proxy|no_proxy)/;

const USER_ENV_KEY = /^[A-Za-z_][A-Za-z0-9_]*$/;
const USER_ENV_DENIED = new Set([
  "PATH",
  "PATHEXT",
  "PYTHONHOME",
  "PYTHONPATH",
  "PYTHONSTARTUP",
  "PYTHONEXECUTABLE",
  "NODE_OPTIONS",
  "NODE_DEBUG",
  "BASH_ENV",
  "ENV",
  "IFS",
  "SHELLOPTS",
  "PERL5OPT",
  "PERL5LIB",
  "RUBYOPT",
  "RUBYLIB",
  "WINDIR",
  "COMSPEC",
  "SYSTEMROOT",
  "PSMODULEPATH",
  "LD_PRELOAD",
  "LD_LIBRARY_PATH",
  "LD_AUDIT",
  "DYLD_INSERT_LIBRARIES",
  "DYLD_LIBRARY_PATH",
  "DYLD_FORCE_FLAT_NAMESPACE",
]);
const USER_ENV_MAX_KEYS = 32;
const USER_ENV_MAX_VALUE = 8192;

function userEnvDenied(key: string): boolean {
  const upper = key.toUpperCase();
  if (USER_ENV_DENIED.has(upper)) return true;
  if (upper.startsWith("LD_") || upper.startsWith("DYLD_")) return true;
  if (upper.startsWith("AGENTCORE_")) return true;
  return false;
}

export function pickRegistryEnv(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!REGISTRY_ENV_KEY.test(key)) continue;
    if (typeof value !== "string") continue;
    out[key] = value;
  }
  return out;
}

/**
 * Ephemeral user env (API keys for this execute). Keep in sync with
 * ``agentcore.core.ephemeral_env``: drop PATH / linker hijacks, keep ``*_API_KEY``.
 */
export function pickUserExecEnv(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (Object.keys(out).length >= USER_ENV_MAX_KEYS) break;
    if (!USER_ENV_KEY.test(key) || key.length > 128) continue;
    if (userEnvDenied(key)) continue;
    if (typeof value !== "string" || value.length > USER_ENV_MAX_VALUE)
      continue;
    out[key] = value;
  }
  return out;
}

/**
 * 杀树后等 pipe 关闭的上限。
 *
 * 孤儿攥着 stdout 时 `close` 可能永不触发，撞上限也照常回话。远小于引擎给本地执行
 * 的 30s slack（`EXEC_TIMEOUT_CAP_S` = 灾难顶 + slack），所以回包不会迟到；同时
 * 保证「杀失败」退化成晚 2s 回话，而不是把 op 挂死在 20 分钟灾难顶之后。
 */
const KILL_GRACE_MS = 2_000;

/**
 * 在 `cwd` 下跑一个脚本文件，捕获 stdout/stderr，超时则强杀。
 *
 * 镜像服务端 SubprocessSandbox：墙钟灾难顶 / 静默活性超时 → stdout 清空、stderr
 * 写超时说明、exit -1；进程起不来（如 PATH 无 python）→ 失败结果而非抛错，保证
 * 通道总收到信封。永不 reject。
 *
 * 超时收的是**整棵进程树**：AI 写的脚本会派生 npm / dev server / 无头浏览器，只杀
 * 解释器会把它们留成孤儿——跑在用户自己机器上占端口占 CPU，还攥着 stdout 让 `close`
 * 永不触发，把 op 拖到超时之后（`test_run` 灾难顶 20 分钟）。
 *
 * 导出仅为测试能用可控的进程树驱动超时路径；产品路径只经 {@link opExecute}。
 */
export function runSubprocess(
  cmd: string[],
  scriptFile: string,
  cwd: string,
  stdin: string | null,
  timeoutSeconds: number,
  startedMs: number,
  envExtra?: Record<string, string>,
  idleTimeoutSeconds?: number | null,
): Promise<WorkspaceOpResult> {
  return new Promise((resolve) => {
    const [bin, ...preArgs] = cmd;
    const child = spawn(bin, [...preArgs, scriptFile], {
      cwd,
      // stdin 是要写入的 pipe（`args.stdin`），不能像 git_run 那样 ignore。
      stdio: ["pipe", "pipe", "pipe"],
      env: envExtra ? { ...process.env, ...envExtra } : undefined,
      ...treeSpawnOptions(),
    });
    let stdout = "";
    let stderr = "";
    let timedOut: "disaster" | "idle" | false = false;
    let settled = false;
    let lastOutputMs = Date.now();
    let graceTimer: ReturnType<typeof setTimeout> | undefined;

    const noteOutput = () => {
      lastOutputMs = Date.now();
    };

    child.stdout.on("data", (chunk: Buffer) => {
      noteOutput();
      if (stdout.length < EXEC_CAPTURE_CAP) stdout += decodePipeChunk(chunk);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      noteOutput();
      if (stderr.length < EXEC_CAPTURE_CAP) stderr += decodePipeChunk(chunk);
    });
    // 进程未读 stdin 即退出会让写入抛 EPIPE——吞掉，不让它变成未捕获错误。
    child.stdin.on("error", () => {});
    // 杀树会把读到一半的管道扯断，那不是执行失败，别让它变成未捕获错误。
    child.stdout.on("error", () => {});
    child.stderr.on("error", () => {});

    const finish = (r: WorkspaceOpResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(disasterTimer);
      if (idleTimer) clearInterval(idleTimer);
      if (graceTimer) clearTimeout(graceTimer);
      resolve(r);
    };

    /** 两种超时各自的信封（stdout 清空 = 与服务端 SubprocessSandbox 同契约）。 */
    const timeoutResult = (kind: "disaster" | "idle"): WorkspaceOpResult =>
      execResult({
        success: false,
        stdout: "",
        stderr:
          kind === "idle"
            ? `Timeout: no output for ${idleTimeoutSeconds}s (execution stalled)`
            : `Timeout: forced stop after ${timeoutSeconds}s (forced stop)`,
        exit_code: -1,
        duration_ms: Date.now() - startedMs,
      });

    /** 收整棵树，然后等 `close`；树没死透就撞 {@link KILL_GRACE_MS} 照常回话。 */
    const killAndAnswer = (kind: "disaster" | "idle") => {
      timedOut = kind;
      void killProcessTree(child);
      graceTimer = setTimeout(() => finish(timeoutResult(kind)), KILL_GRACE_MS);
    };

    const disasterTimer = setTimeout(() => {
      if (settled || timedOut) return;
      killAndAnswer("disaster");
    }, timeoutSeconds * 1000);

    let idleTimer: ReturnType<typeof setInterval> | undefined;
    const idleLimitMs =
      idleTimeoutSeconds != null && idleTimeoutSeconds > 0
        ? idleTimeoutSeconds * 1000
        : null;
    if (idleLimitMs != null) {
      idleTimer = setInterval(() => {
        if (settled || timedOut) return;
        if (Date.now() - lastOutputMs >= idleLimitMs) {
          killAndAnswer("idle");
        }
      }, 200);
    }

    child.on("error", (err) => {
      // 杀树自身触发的 error 不能改写超时信封。
      if (timedOut) {
        finish(timeoutResult(timedOut));
        return;
      }
      // Spawn-time EACCES/EPERM: declare at this site (err.code, not message
      // matching). A process that already has a pid is not a refused start.
      const spawnDenied = child.pid == null && isSpawnDeniedError(err);
      finish(
        execResult({
          success: false,
          stdout,
          stderr:
            stderr ||
            (spawnDenied
              ? spawnDeniedStderr(err.message)
              : `Failed to start process: ${err.message}`),
          exit_code: -1,
          duration_ms: Date.now() - startedMs,
        }),
      );
    });
    child.on("close", (code) => {
      if (timedOut) {
        finish(timeoutResult(timedOut));
        return;
      }
      finish(
        execResult({
          success: code === 0,
          stdout,
          stderr,
          exit_code: code ?? 0,
          duration_ms: Date.now() - startedMs,
        }),
      );
    });

    if (stdin != null) child.stdin.write(stdin);
    child.stdin.end();
  });
}

export async function opExecute(
  root: StoredRoot,
  args: Record<string, unknown>,
): Promise<WorkspaceOpResult> {
  const startedMs = Date.now();
  const language = String(args.language ?? "python");
  const lang = EXEC_LANGS[language];
  if (!lang) {
    return execResult({
      success: false,
      stdout: "",
      stderr: `Unsupported language: ${language}`,
      exit_code: 1,
      duration_ms: 0,
    });
  }

  const resolved = resolveLangCmd(language, lang);
  if ("error" in resolved) {
    return execResult({
      success: false,
      stdout: "",
      stderr: resolved.error,
      exit_code: 127,
      duration_ms: Date.now() - startedMs,
    });
  }

  const code = String(args.code ?? "");
  const stdin = args.stdin == null ? null : String(args.stdin);
  // 通道上限 = EXEC_TIMEOUT_CAP_S（须覆盖 test_run 灾难顶 20min + slack）；
  // 工具层自己的上限（code_execute ≤60）在服务端 clamp，本处只兑现请求方给出的秒数。
  const timeoutSeconds = Math.max(
    1,
    Math.min(Number(args.timeout_seconds ?? 30), EXEC_TIMEOUT_CAP_S),
  );
  const idleRaw = args.idle_timeout_seconds;
  const idleTimeoutSeconds =
    idleRaw == null || idleRaw === ""
      ? null
      : Math.max(1, Math.min(Number(idleRaw), timeoutSeconds));

  // cwd = 工作区子路径（工作区对称化 D1a）：把进程工作目录定到该子树，使本地执行与文件工具
  // 同目录（呼应服务端 cwd=workspace）。`""` / `"."` = 绑定根自身（现行为）。子树尚不存在
  // （裸聊懒建后还没产文件就先执行）→ 回退根，避免用不存在的 cwd 拉起进程而失败。
  const cwdRel = String(args.cwd ?? "");
  const sub = cwdRel === "." ? "" : cwdRel.replace(/^\/+|\/+$/g, "");
  let cwdAbs = root.absPath;
  if (sub) {
    const resolvedPath = resolveLexical(root, sub);
    const real = resolvedPath ? await realInside(root, resolvedPath) : null;
    if (real?.ok) cwdAbs = real.path;
  }

  // 脚本写入临时目录（与服务端一致：代码文件在临时区，进程 cwd 才是工作区）。
  let tmpDir: string;
  try {
    tmpDir = await fs.mkdtemp(join(tmpdir(), "agentcore-exec-"));
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  }
  try {
    const scriptFile = join(tmpDir, `main${lang.ext}`);
    await fs.writeFile(scriptFile, code, "utf-8");
    // W3: inject AGENTCORE_EXTERNAL_* + D11′ PYTHONPATH (. + src/lib) so local
    // code_execute can import src-layout packages the same way TestExitCode does.
    // Registry/cache pins from server (test_run install) are whitelist-merged only.
    // Ephemeral user env (API keys) is denylist-merged: PATH / LD_* still dropped.
    const envExtra: Record<string, string> = {
      ...buildExternalEnvFromRoots(
        args.external_roots as Record<string, unknown> | undefined,
        String(args.conversation_id ?? ""),
      ),
      ...pickRegistryEnv(args.env),
      ...pickUserExecEnv(args.env),
    };
    if (language === "python") {
      Object.assign(envExtra, buildWorkspacePythonpathEnv(cwdAbs));
    }
    // 产物写回：截止值必须在子进程能碰盘之前取。
    const cutoffMs = writtenScanCutoffMs();
    const ran = await runSubprocess(
      resolved.cmd,
      scriptFile,
      cwdAbs,
      stdin,
      timeoutSeconds,
      startedMs,
      Object.keys(envExtra).length > 0 ? envExtra : undefined,
      idleTimeoutSeconds,
    );
    return await withWrittenFiles(ran, {
      rootAbs: root.absPath,
      cwdAbs,
      cutoffMs,
    });
  } catch (e) {
    return opErr("WorkspaceIOError", toReason(e));
  } finally {
    await fs.rm(tmpDir, { recursive: true, force: true }).catch(() => {});
  }
}
