/**
 * 终端 tab M2：从现有 store 派生「一次性执行记录」列表（指针/选择器，非权威副本）。
 *
 * 权威态：单 Agent → message.process 工具步；多 Agent → execution 投影的 agent.toolCalls。
 * 仅聚合 `code_execute` / `test_run`（长进程走 `terminal` / backgroundProcesses）。
 */

import { resolveToolWireStatus } from "@/lib/channelRedirect";
import { assistantProjectionId } from "@/stores/conversation/runtime";
import type { Message } from "@/stores/conversation/types";
import {
  type Execution,
  type ExecutionRuntime,
  type RunFrame,
  type ToolCallState,
  projectRuntime,
} from "@/stores/execution";
import type { ProcessStep, ToolDisplay } from "@/types/events";

/** 进入终端观测面的一次性执行工具。 */
export const EXECUTION_RECORD_TOOLS = new Set(["code_execute", "test_run"]);

export type ExecutionRecordStatus = "running" | "success" | "error";

export interface ExecutionRecord {
  /** = tool_call_id（跨对话唯一足够用于选中）。 */
  id: string;
  toolName: string;
  /** 行摘要：命令 / 语言 / purpose。 */
  summary: string;
  /** Agent 角色（单聊为「助手」）。 */
  agentRole: string;
  status: ExecutionRecordStatus;
  /** 多 Agent worker 调用才有 —— 供「跳 run 详情」。 */
  runId?: string;
  /** 回合投影键（`assistantProjectionId`），与 sidePanel / execution.byId 对齐。 */
  messageId: string;
  /** 权威结束态输出（来自 tool_use_end.display）；running 时可能为空。 */
  stdout: string;
  stderr: string;
  exitCode: number | null;
  /** 工具步出现在回合内的次序键（消息序 × 步序），用于时间序平铺。 */
  orderKey: number;
}

function asStr(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function isExecTool(name: string): boolean {
  return EXECUTION_RECORD_TOOLS.has(name);
}

/** 从 display / args 抽命令或语言摘要。 */
export function executionRecordSummary(
  toolName: string,
  args: Record<string, unknown>,
  display?: ToolDisplay | null,
): string {
  const purpose = asStr(args.purpose).trim();
  if (purpose) return purpose;

  if (toolName === "test_run") {
    const cmd =
      asStr(display?.command).trim() ||
      asStr(args.command).trim() ||
      asStr(args.argv);
    if (cmd) return cmd;
    const fw = asStr(display?.framework).trim() || asStr(args.framework).trim();
    return fw ? `测试 · ${fw}` : "运行测试";
  }

  const language =
    asStr(display?.language).trim() || asStr(args.language).trim();
  const code = asStr(args.code).trim();
  const firstLine =
    code
      .split("\n")
      .find((l) => l.trim())
      ?.trim() ?? "";
  if (language && firstLine) {
    const short =
      firstLine.length > 48 ? `${firstLine.slice(0, 48)}…` : firstLine;
    return `${language} · ${short}`;
  }
  if (language) return language;
  if (firstLine) {
    return firstLine.length > 56 ? `${firstLine.slice(0, 56)}…` : firstLine;
  }
  return toolName === "code_execute" ? "Run code" : toolName;
}

export function outputFromDisplay(display?: ToolDisplay | null): {
  stdout: string;
  stderr: string;
  exitCode: number | null;
} {
  if (!display) return { stdout: "", stderr: "", exitCode: null };
  const exitRaw = display.exit_code;
  return {
    stdout: asStr(display.stdout),
    stderr: asStr(display.stderr),
    exitCode: typeof exitRaw === "number" ? exitRaw : null,
  };
}

function statusFromTool(
  status: "running" | "success" | "error" | "redirect",
  exitCode: number | null,
): ExecutionRecordStatus {
  if (status === "running") return "running";
  if (status === "error") return "error";
  if (exitCode != null && exitCode !== 0) return "error";
  return "success";
}

/** 从 frame 流找 tool_use_start 的 runId（worker 标签；空串视为无）。 */
export function runIdFromFrames(
  frames: RunFrame[],
  toolCallId: string,
): string | undefined {
  for (const f of frames) {
    if (f.kind === "tool_use_start" && f.toolCallId === toolCallId) {
      return f.runId ? f.runId : undefined;
    }
  }
  return undefined;
}

function recordFromProcessTool(
  step: Extract<ProcessStep, { kind: "tool" }>,
  messageId: string,
  orderKey: number,
  agentRole: string,
): ExecutionRecord | null {
  if (!isExecTool(step.tool_name)) return null;
  if (resolveToolWireStatus(step.status, step.failure) === "redirect")
    return null;
  const out = outputFromDisplay(step.display);
  return {
    id: step.id,
    toolName: step.tool_name,
    summary: executionRecordSummary(
      step.tool_name,
      step.arguments ?? {},
      step.display,
    ),
    agentRole,
    status: statusFromTool(step.status, out.exitCode),
    messageId,
    stdout: out.stdout,
    stderr: out.stderr,
    exitCode: out.exitCode,
    orderKey,
  };
}

function recordFromToolCall(
  tc: ToolCallState,
  messageId: string,
  orderKey: number,
  agentRole: string,
  runId: string | undefined,
): ExecutionRecord | null {
  if (!isExecTool(tc.toolName)) return null;
  if (tc.status === "redirect") return null;
  const out = outputFromDisplay(tc.display);
  return {
    id: tc.id,
    toolName: tc.toolName,
    summary: executionRecordSummary(
      tc.toolName,
      tc.arguments ?? {},
      tc.display,
    ),
    agentRole,
    status: statusFromTool(tc.status, out.exitCode),
    runId,
    messageId,
    stdout: out.stdout,
    stderr: out.stderr,
    exitCode: out.exitCode,
    orderKey,
  };
}

/** 从单条助手消息的 process 时间线抽取。 */
export function recordsFromProcess(
  process: ProcessStep[] | undefined,
  messageId: string,
  orderBase: number,
  agentRole = "助手",
): ExecutionRecord[] {
  if (!process?.length) return [];
  const out: ExecutionRecord[] = [];
  let i = 0;
  for (const step of process) {
    if (step.kind !== "tool") continue;
    const rec = recordFromProcessTool(
      step,
      messageId,
      orderBase + i,
      agentRole,
    );
    i += 1;
    if (rec) out.push(rec);
  }
  return out;
}

/** 从多 Agent execution 投影抽取（含各 worker；CEO 直调仍在 process）。 */
export function recordsFromExecution(
  execution: Execution | null,
  messageId: string,
  orderBase: number,
  frames: RunFrame[] = [],
): ExecutionRecord[] {
  if (!execution) return [];
  const out: ExecutionRecord[] = [];
  let i = 0;
  for (const agent of execution.agents) {
    for (const tc of agent.toolCalls) {
      const runId = runIdFromFrames(frames, tc.id);
      const rec = recordFromToolCall(
        tc,
        messageId,
        orderBase + i,
        agent.role || "助手",
        runId,
      );
      i += 1;
      if (rec) out.push(rec);
    }
  }
  return out;
}

/**
 * 本对话全部执行记录（时间序平铺）。
 * `executionById` 以 `assistantProjectionId` 为键；无 slot 的回合只贡献 process。
 */
export function deriveExecutionRecords(
  messages: Message[],
  executionById: Record<string, ExecutionRuntime>,
): ExecutionRecord[] {
  const out: ExecutionRecord[] = [];
  let orderBase = 0;
  for (const msg of messages) {
    if (msg.role !== "assistant") continue;
    const messageId = assistantProjectionId(msg);
    const fromProcess = recordsFromProcess(
      msg.process,
      messageId,
      orderBase,
      "助手",
    );
    orderBase += (msg.process?.length ?? 0) + 1;

    const rt = executionById[messageId];
    const exec = rt ? projectRuntime(rt) : null;
    const fromExec = recordsFromExecution(
      exec,
      messageId,
      orderBase,
      rt?.frames ?? [],
    );
    orderBase += 1000;

    // process 与 execution 不重叠（worker 带 run_id 不进 process）；仍按 id 去重防双折。
    const seen = new Set(out.map((r) => r.id));
    for (const r of [...fromProcess, ...fromExec]) {
      if (seen.has(r.id)) continue;
      seen.add(r.id);
      out.push(r);
    }
  }
  out.sort((a, b) => a.orderKey - b.orderKey);
  return out;
}

/** 合并 live chunk 与权威 display 成滚屏文本。 */
export function resolveRecordOutput(
  record: ExecutionRecord,
  liveStdout: string,
  liveStderr: string,
): string {
  if (record.status === "running") {
    const parts: string[] = [];
    if (liveStdout) parts.push(liveStdout);
    if (liveStderr) parts.push(liveStderr);
    return parts.join("") || "";
  }
  // 结束态：权威 display；无 display 时回落 live（罕见竞态）。
  const stdout = record.stdout || liveStdout;
  const stderr = record.stderr || liveStderr;
  const parts: string[] = [];
  if (stdout) parts.push(stdout.replace(/\n+$/, ""));
  if (stderr) {
    if (parts.length) parts.push("");
    parts.push(stderr.replace(/\n+$/, ""));
  }
  if (record.exitCode != null) {
    if (parts.length) parts.push("");
    parts.push(`退出码 ${record.exitCode}`);
  }
  return parts.join("\n");
}
