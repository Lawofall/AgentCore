/**
 * 后台进程服务 —— spawn 长命令、环形输出 buffer、杀进程树、按 conversation_id 记账。
 *
 * 与 `terminal-service.ts`（外置终端）并列，不混装。应用退出一律终止（MVP）。
 */
import { type ChildProcess, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import {
  PROCESS_CHANNELS,
  type ProcessEventPush,
  type ProcessListItem,
  type ProcessListValue,
  type ProcessOpValue,
  type ProcessStatus,
} from "@shared/process-contract";
import { BrowserWindow, app, ipcMain } from "electron";
import { resolveCwdInside } from "./fs/pathGuard";
import type { StoredRoot } from "./fs/roots";
import {
  RUN_SHELL_UNAVAILABLE,
  resolveRunShell,
  runShellArgv,
  runShellEnvExtra,
} from "./fs/workspace/execCodec";
import { requireStringFields } from "./ipc-validate";
import {
  killProcessTree,
  killProcessTreeSync,
  treeSpawnOptions,
} from "./proc-tree";
import { ptyService, stripAnsi } from "./pty-service";

/** `wait_for` 匹配前剥 ANSI，避免 Vite 等把 `Local:` 拆成 `Local\x1b[22m:` 假阴性。 */
export function bufferMatchesWait(buffer: string, re: RegExp): boolean {
  return re.test(stripAnsi(buffer));
}

/** 单进程环形输出上限（约 1MB）。 */
export const PROCESS_BUFFER_CAP = 1024 * 1024;

/** 单对话并发运行上限。 */
export const PROCESS_CONCURRENCY_CAP = 5;

/** `wait_for` 默认超时（秒）。 */
const DEFAULT_WAIT_TIMEOUT_S = 30;

export interface ProcessRecord {
  process_id: string;
  conversation_id: string;
  name?: string;
  command: string;
  cwd: string;
  status: ProcessStatus;
  started_at: string;
  exit_code: number | null;
  buffer: string;
  child: ChildProcess | null;
}

export interface StartProcessArgs {
  conversation_id: string;
  command: string;
  cwd: string;
  name?: string;
  wait_for?: string;
  wait_timeout_seconds?: number;
}

export interface ReadProcessArgs {
  process_id: string;
  wait_for?: string;
  wait_timeout_seconds?: number;
  tail_lines?: number;
}

/** 环形截断：超长时丢弃头部，保留尾部 cap 字节（UTF-16 码元近似）。 */
export function appendRingBuffer(
  current: string,
  chunk: string,
  cap = PROCESS_BUFFER_CAP,
): string {
  if (!chunk) return current;
  const next = current + chunk;
  if (next.length <= cap) return next;
  return next.slice(next.length - cap);
}

/** 取末尾 N 行（N≤0 或非有限 → 全文）。 */
export function tailLines(text: string, n?: number): string {
  if (n == null || !Number.isFinite(n) || n <= 0) return text;
  const lines = text.split("\n");
  if (lines.length <= n) return text;
  return lines.slice(-n).join("\n");
}

function broadcast(event: ProcessEventPush): void {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) {
      win.webContents.send(PROCESS_CHANNELS.event, event);
    }
  }
}

function spawnShell(command: string, cwd: string): ChildProcess {
  // Same launcher as short/verify ``run`` (Git Bash / PowerShell / PATH bash).
  // treeSpawnOptions：POSIX 新进程组，便于杀树（Windows 走 taskkill /T 无需标志）；
  // 仍由本服务持有引用，应用退出会主动杀。
  const spec = resolveRunShell();
  if (!spec) {
    throw new Error(RUN_SHELL_UNAVAILABLE);
  }
  return spawn(spec.file, runShellArgv(spec, command), {
    cwd,
    env: { ...process.env, ...runShellEnvExtra() },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    ...treeSpawnOptions(),
  });
}

function toListItem(rec: ProcessRecord): ProcessListItem {
  return {
    process_id: rec.process_id,
    name: rec.name,
    command: rec.command,
    status: rec.status,
    started_at: rec.started_at,
    exit_code: rec.exit_code ?? undefined,
  };
}

function toOpValue(
  rec: ProcessRecord,
  opts?: { matched?: boolean; output?: string },
): ProcessOpValue {
  return {
    process_id: rec.process_id,
    status: rec.status,
    output: opts?.output ?? rec.buffer,
    ...(opts?.matched !== undefined ? { matched: opts.matched } : {}),
    ...(rec.exit_code !== null && rec.status === "exited"
      ? { exit_code: rec.exit_code }
      : rec.exit_code != null
        ? { exit_code: rec.exit_code }
        : {}),
  };
}

function compileWaitRegex(pattern: string): RegExp | null {
  try {
    return new RegExp(pattern);
  } catch {
    return null;
  }
}

class ProcessService {
  private readonly byId = new Map<string, ProcessRecord>();

  runningCount(conversationId: string): number {
    let n = 0;
    for (const r of this.byId.values()) {
      if (r.conversation_id === conversationId && r.status === "running") n++;
    }
    return n;
  }

  get(processId: string): ProcessRecord | undefined {
    return this.byId.get(processId);
  }

  list(conversationId: string): ProcessListValue {
    const processes: ProcessListItem[] = [];
    for (const r of this.byId.values()) {
      if (r.conversation_id === conversationId) processes.push(toListItem(r));
    }
    processes.sort((a, b) => a.started_at.localeCompare(b.started_at));
    return { processes };
  }

  /**
   * Spawn 立即完成；若带 `wait_for`，等命中或超时后再 resolve（进程继续跑）。
   */
  async start(
    args: StartProcessArgs,
  ): Promise<
    | { ok: true; value: ProcessOpValue }
    | { ok: false; error: { kind: string; detail: string } }
  > {
    const conversationId = args.conversation_id.trim();
    const command = args.command.trim();
    if (!conversationId) {
      return {
        ok: false,
        error: { kind: "WorkspaceIOError", detail: "缺少 conversation_id" },
      };
    }
    if (!command) {
      return {
        ok: false,
        error: { kind: "WorkspaceIOError", detail: "command 不能为空" },
      };
    }
    if (this.runningCount(conversationId) >= PROCESS_CONCURRENCY_CAP) {
      return {
        ok: false,
        error: {
          kind: "WorkspaceIOError",
          detail: `本对话后台进程已达上限（${PROCESS_CONCURRENCY_CAP}）`,
        },
      };
    }

    const processId = randomUUID();
    const rec: ProcessRecord = {
      process_id: processId,
      conversation_id: conversationId,
      name: args.name?.trim() || undefined,
      command,
      cwd: args.cwd,
      status: "running",
      started_at: new Date().toISOString(),
      exit_code: null,
      buffer: "",
      child: null,
    };
    this.byId.set(processId, rec);

    let child: ChildProcess;
    try {
      child = spawnShell(command, args.cwd);
    } catch (e) {
      rec.status = "exited";
      rec.exit_code = -1;
      rec.buffer = appendRingBuffer(
        rec.buffer,
        `Failed to start: ${e instanceof Error ? e.message : String(e)}`,
      );
      return { ok: true, value: toOpValue(rec) };
    }
    rec.child = child;

    const onChunk = (chunk: Buffer) => {
      const text = chunk.toString("utf-8");
      rec.buffer = appendRingBuffer(rec.buffer, text);
      broadcast({
        type: "output",
        process_id: processId,
        conversation_id: conversationId,
        chunk: text,
      });
    };
    child.stdout?.on("data", onChunk);
    child.stderr?.on("data", onChunk);

    child.on("error", (err) => {
      rec.buffer = appendRingBuffer(
        rec.buffer,
        `\n[process error] ${err.message}\n`,
      );
      if (rec.status === "running") {
        rec.status = "exited";
        rec.exit_code = -1;
        rec.child = null;
        broadcast({
          type: "exited",
          process_id: processId,
          conversation_id: conversationId,
          exit_code: -1,
        });
      }
    });

    child.on("close", (code) => {
      if (rec.status === "exited") return;
      rec.status = "exited";
      rec.exit_code = code ?? 0;
      rec.child = null;
      broadcast({
        type: "exited",
        process_id: processId,
        conversation_id: conversationId,
        exit_code: rec.exit_code,
      });
    });

    broadcast({
      type: "started",
      process_id: processId,
      conversation_id: conversationId,
      item: toListItem(rec),
    });

    if (!args.wait_for) {
      return { ok: true, value: toOpValue(rec) };
    }

    const re = compileWaitRegex(args.wait_for);
    if (!re) {
      return {
        ok: false,
        error: {
          kind: "WorkspaceIOError",
          detail: `非法 wait_for 正则：${args.wait_for}`,
        },
      };
    }
    const timeoutS =
      args.wait_timeout_seconds != null &&
      Number.isFinite(args.wait_timeout_seconds) &&
      args.wait_timeout_seconds > 0
        ? args.wait_timeout_seconds
        : DEFAULT_WAIT_TIMEOUT_S;

    const matched = await this.waitForMatch(rec, re, timeoutS);
    return { ok: true, value: toOpValue(rec, { matched }) };
  }

  async read(
    args: ReadProcessArgs,
  ): Promise<
    | { ok: true; value: ProcessOpValue }
    | { ok: false; error: { kind: string; detail: string } }
  > {
    const rec = this.byId.get(args.process_id);
    if (!rec) {
      return {
        ok: false,
        error: { kind: "WorkspaceIOError", detail: "进程不存在或已清理" },
      };
    }

    if (!args.wait_for) {
      return {
        ok: true,
        value: toOpValue(rec, {
          output: tailLines(rec.buffer, args.tail_lines),
        }),
      };
    }

    const re = compileWaitRegex(args.wait_for);
    if (!re) {
      return {
        ok: false,
        error: {
          kind: "WorkspaceIOError",
          detail: `非法 wait_for 正则：${args.wait_for}`,
        },
      };
    }
    const timeoutS =
      args.wait_timeout_seconds != null &&
      Number.isFinite(args.wait_timeout_seconds) &&
      args.wait_timeout_seconds > 0
        ? args.wait_timeout_seconds
        : DEFAULT_WAIT_TIMEOUT_S;

    // 从调用时的 buffer 长度起等新输出命中；已有内容也参与匹配（与 start 一致）。
    const matched = await this.waitForMatch(rec, re, timeoutS);
    return {
      ok: true,
      value: toOpValue(rec, {
        matched,
        output: tailLines(rec.buffer, args.tail_lines),
      }),
    };
  }

  stop(processId: string): ProcessOpValue | null {
    const rec = this.byId.get(processId);
    if (!rec) return null;
    this.terminate(rec, "async");
    return toOpValue(rec);
  }

  killConversation(conversationId: string): void {
    const ids: string[] = [];
    for (const r of this.byId.values()) {
      if (r.conversation_id === conversationId) ids.push(r.process_id);
    }
    for (const id of ids) {
      this.stop(id);
      this.byId.delete(id);
    }
  }

  /**
   * 应用退出：**同步**杀树。`before-quit` 之后事件循环未必再转，异步 kill 可能在
   * taskkill 起来之前就随进程一起没了，把 dev server 之流留在用户机器上。
   */
  killAll(): void {
    for (const rec of this.byId.values()) {
      this.terminate(rec, "sync");
    }
    this.byId.clear();
  }

  /**
   * 杀掉该记录的**整棵进程树**并落账。
   *
   * `npm run dev` 这类命令真正占端口的是 shell 的孙进程，只杀 shell 会留下孤儿。
   * 活路径用异步 kill——Windows 的 `taskkill` 不该把 Electron 主进程卡住；同步变体
   * 只给退出钩子（见 {@link killAll}）。
   */
  private terminate(rec: ProcessRecord, mode: "async" | "sync"): void {
    const child = rec.child;
    if (rec.status !== "running" || !child?.pid) return;
    if (mode === "sync") {
      killProcessTreeSync(child);
    } else {
      void killProcessTree(child);
    }
    // close handler 会落账；若进程已僵死则本地收口
    if (rec.status === "running") {
      rec.status = "exited";
      rec.exit_code = rec.exit_code ?? -1;
      rec.child = null;
      broadcast({
        type: "exited",
        process_id: rec.process_id,
        conversation_id: rec.conversation_id,
        exit_code: rec.exit_code,
      });
    }
  }

  /** 等 regex 命中当前 buffer、进程退出、或超时。 */
  private waitForMatch(
    rec: ProcessRecord,
    re: RegExp,
    timeoutSeconds: number,
  ): Promise<boolean> {
    if (bufferMatchesWait(rec.buffer, re)) return Promise.resolve(true);
    if (rec.status === "exited") {
      return Promise.resolve(bufferMatchesWait(rec.buffer, re));
    }

    return new Promise((resolveMatch) => {
      let settled = false;
      const finish = (matched: boolean) => {
        if (settled) return;
        settled = true;
        clearInterval(poll);
        clearTimeout(timer);
        resolveMatch(matched);
      };

      const poll = setInterval(() => {
        if (bufferMatchesWait(rec.buffer, re)) {
          finish(true);
          return;
        }
        if (rec.status === "exited") {
          finish(bufferMatchesWait(rec.buffer, re));
        }
      }, 50);

      const timer = setTimeout(
        () => finish(bufferMatchesWait(rec.buffer, re)),
        timeoutSeconds * 1000,
      );
    });
  }
}

/** 模块级单例 —— workspace op 与 IPC 共用。 */
export const processService = new ProcessService();

/** 解析 cwd：词法 + realpath 双守卫（与 pty / 写路径同级）。 */
export async function resolveProcessCwd(
  root: StoredRoot,
  cwdArg: string | undefined,
): Promise<{ ok: true; cwd: string } | { ok: false; detail: string }> {
  return resolveCwdInside(root, cwdArg);
}

let quitHooked = false;

export function registerProcessIpc(): void {
  ipcMain.handle(PROCESS_CHANNELS.list, (_e, p: unknown) => {
    const args = requireStringFields(p, ["conversation_id"]);
    if (!args) return { processes: [] };
    return processService.list(args.conversation_id);
  });

  ipcMain.handle(PROCESS_CHANNELS.stop, (_e, p: unknown) => {
    const args = requireStringFields(p, ["process_id"]);
    if (!args) {
      return {
        process_id: "",
        status: "exited" as const,
        output: "",
        exit_code: -1,
      };
    }
    const value = processService.stop(args.process_id);
    if (!value) {
      return {
        process_id: args.process_id,
        status: "exited" as const,
        output: "",
        exit_code: -1,
      };
    }
    return value;
  });

  ipcMain.handle(PROCESS_CHANNELS.read, async (_e, p: unknown) => {
    const args = requireStringFields(p, ["process_id"]);
    if (!args) {
      return {
        ok: false,
        error: { kind: "WorkspaceIOError", detail: "无效参数" },
      };
    }
    const tail =
      p && typeof p === "object" && "tail_lines" in p
        ? Number((p as { tail_lines?: unknown }).tail_lines)
        : undefined;
    const result = await processService.read({
      process_id: args.process_id,
      tail_lines: Number.isFinite(tail) ? tail : undefined,
    });
    return result.ok
      ? result.value
      : {
          process_id: args.process_id,
          status: "exited",
          output: "",
          exit_code: -1,
        };
  });

  ipcMain.handle(PROCESS_CHANNELS.killConversation, (_e, p: unknown) => {
    const args = requireStringFields(p, ["conversation_id"]);
    if (!args) return;
    processService.killConversation(args.conversation_id);
    // 删对话时一并清用户终端（与 pty:killConversation 同姿态；双通道幂等）。
    ptyService.killConversation(args.conversation_id);
  });

  if (!quitHooked) {
    quitHooked = true;
    app.on("before-quit", () => {
      processService.killAll();
      ptyService.killAll();
    });
  }
}
