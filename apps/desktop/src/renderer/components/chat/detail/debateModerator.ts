/**
 * 辩论主持人侧面板：从既有 execution 投影推导主持人身份。
 * 识别与协作图 {@link debateModeratorId} 同源。过程时间线走同一套 ProcessTimeline。
 */
import { debateModeratorId } from "@/components/graph/helpers";
import type { AgentState, Execution } from "@/stores/execution";

/** 是否渲染「思考中」空占位：声明 thinking=false 的 run 不占位；已有 reasoning 仍由调用方照常显示。 */
export function isThinkingLivePlaceholder(
  agent: Pick<
    AgentState,
    "thinking" | "status" | "outputChunks" | "toolProgress"
  >,
): boolean {
  if (!agent.thinking) return false;
  return (
    agent.status === "working" &&
    agent.outputChunks.join("").length === 0 &&
    !agent.toolProgress
  );
}

/** 当前回合辩论主持人 run id；非辩论 / 尚无法从投影推导 → null。 */
export function resolveDebateModeratorRunId(
  execution: Pick<Execution, "runs" | "debate">,
): string | null {
  const settled = execution.debate?.moderator_run_id;
  if (settled) return settled;
  return debateModeratorId(execution.runs, null);
}

export function isDebateModeratorRun(
  execution: Execution,
  runId: string,
): boolean {
  const modId = resolveDebateModeratorRunId(execution);
  return modId != null && modId === runId;
}
