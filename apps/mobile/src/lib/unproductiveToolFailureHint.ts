import type { ProcessStep } from "@agentcore/contract-types";

/**
 * 有正文的 unproductive：从 process 收集 status=error 的工具名。
 * 手机无桌面 journal.runProcesses，不扫 events、不扩契约。
 */
export function collectFailedToolNames(
  process: ProcessStep[] | undefined,
): string[] {
  if (!process?.length) return [];
  const names: string[] = [];
  for (const step of process) {
    if (step.kind === "tool" && step.status === "error") {
      names.push(step.tool_name);
    }
  }
  return names;
}

/**
 * 窄触发：有失败工具 + finish_reason=unproductive + 非空正文。
 * 空正文已有失败横幅时勿叠第二条。
 */
export function shouldShowUnproductiveToolFailureHint(opts: {
  finishReason: string | null | undefined;
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
 * 密度低于桌面「本轮有 N 个…」——气泡顶栏 chip 已写无有效进展。
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
  return count === 1 ? `${list} 未成功` : `${count} 个工具未成功：${list}`;
}
