import { resolveToolWireStatus } from "@/lib/channelRedirect";
import type { ExecutionJournal } from "@/stores/execution/types";
import type { ProcessStep } from "@/types/events";

/**
 * B′：unproductive 假完成拆穿——从 process（优先）收集 status=error 的工具名。
 * journal.runProcesses 仅在 process 无失败时作窄回退（captain 无 run_id 的 worker
 * 失败可能不在主 process）；不扫 events、不扩契约。
 */
export function collectFailedToolNames(
  process: ProcessStep[] | undefined,
  journal?: ExecutionJournal | null,
): string[] {
  const fromProcess = failedNamesFromSteps(process);
  if (fromProcess.length > 0) return fromProcess;

  const runProcesses = journal?.runProcesses;
  if (!runProcesses) return [];

  const names: string[] = [];
  for (const steps of Object.values(runProcesses)) {
    names.push(...failedNamesFromSteps(steps));
  }
  return names;
}

function failedNamesFromSteps(steps: ProcessStep[] | undefined): string[] {
  if (!steps?.length) return [];
  const names: string[] = [];
  for (const step of steps) {
    if (
      step.kind === "tool" &&
      resolveToolWireStatus(step.status, step.failure) === "error"
    ) {
      names.push(step.tool_name);
    }
  }
  return names;
}

/**
 * 窄触发：有失败工具 + finish_reason=unproductive + 非空正文。
 * 空正文已有合成失败卡时勿叠 B′ 条。
 */
export function shouldShowUnproductiveToolFailureHint(opts: {
  finishReason: string | undefined;
  content: string | undefined;
  failedToolNames: readonly string[];
}): boolean {
  if (opts.finishReason !== "unproductive") return false;
  if (opts.failedToolNames.length === 0) return false;
  if (!(opts.content ?? "").trim()) return false;
  return true;
}

/**
 * 人话短句：次数 + 工具展示名。去重展示名，次数按失败行计。
 * `labelOf` 默认原样 tool_name；UI 可注入 toolMeta label。
 */
export function formatUnproductiveToolFailureHint(
  failedToolNames: readonly string[],
  labelOf: (toolName: string) => string = (n) => n,
): string | null {
  if (failedToolNames.length === 0) return null;
  const count = failedToolNames.length;
  const labels: string[] = [];
  const seen = new Set<string>();
  for (const name of failedToolNames) {
    const label = labelOf(name);
    if (seen.has(label)) continue;
    seen.add(label);
    labels.push(label);
  }
  const list = labels.join("、");
  return count === 1
    ? `本轮有 1 个工具未成功：${list}`
    : `本轮有 ${count} 个工具未成功：${list}`;
}
