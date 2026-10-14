import { isDebate, type Execution, type RunNode } from "@/stores/execution";

export interface ConversationGraphTurn {
  messageId: string;
  execution: Execution | null;
}

export interface ConversationGraphFold {
  execution: Execution | null;
  /** Run id → the assistant message whose journal owns that segment. */
  owners: ReadonlyMap<string, string>;
}

const SETTLED = new Set([
  "completed",
  "failed",
  "cancelled",
  "skipped",
  "cut",
]);

/**
 * One conversation, one graph. Each turn's non-debate segments stay, in
 * plan order, joined by the dependencies already on the runs. Captains are
 * drawn as ordinary segments so a later opening is not dropped as a sink.
 */
export function foldConversationGraphExecutions(
  turns: readonly ConversationGraphTurn[],
): ConversationGraphFold {
  const owners = new Map<string, string>();
  const runs: RunNode[] = [];
  const agents: Execution["agents"] = [];
  const seenAgents = new Set<string>();
  let shell: Execution | null = null;

  for (const turn of turns) {
    const execution = turn.execution;
    if (!execution || isDebate(execution)) continue;
    shell ??= execution;
    for (const agent of execution.agents) {
      if (seenAgents.has(agent.id)) continue;
      seenAgents.add(agent.id);
      agents.push(agent);
    }
    for (const run of execution.runs) {
      if (run.kind === "captain") continue;
      // Declared work stays off the canvas until its call has started.
      if (run.status === "pending") continue;
      if (owners.has(run.id)) continue;
      owners.set(run.id, turn.messageId);
      runs.push({
        ...run,
        kind: "agent",
        parentRunId: null,
        continuesRunId: null,
        continuationIndex: 0,
        delegateBatch: 1,
        actId: "act-1",
        stance: null,
        group: null,
        round: 0,
        sideKey: null,
      });
    }
  }

  if (!shell || runs.length === 0) return { execution: null, owners };

  const open = runs.some(
    (run) => run.status === "pending" || run.status === "running",
  );
  return {
    owners,
    execution: {
      ...shell,
      id: "conversation",
      planType: "single_agent",
      taskSummary: "",
      status: open ? "running" : "completed",
      prevExecutionId: null,
      agents,
      runs,
      acts: [],
      progress: {
        completed: runs.filter((run) => SETTLED.has(run.status)).length,
        total: runs.length,
      },
      batches: [],
      debate: null,
      debateRounds: [],
      crossExamEnabled: false,
      debateOpening: null,
      evidenceLedger: [],
    },
  };
}
