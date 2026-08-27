import type { Execution, RunStatus } from "@/stores/execution";
import type {
  CoordinationWaitPayload,
  TeamSynthesisPreviewPayload,
} from "@/types/events";

/**
 * Same membership as {@link deriveCaptainStatus}'s WORKER_TERMINAL.
 * Do not count only `completed` — failed / cancelled / skipped are done too.
 */
const WORKER_TERMINAL = new Set<string>([
  "completed",
  "failed",
  "cancelled",
  "skipped",
]);

/** Worker runs only (CEO captain sink is not a delegate progress unit). */
export function workerProgress(execution: Execution): {
  completed: number;
  total: number;
} {
  const workers = execution.runs.filter((r) => r.kind !== "captain");
  return {
    completed: workers.filter((r) => r.status === "completed").length,
    total: workers.length,
  };
}

/** True when every non-captain run is in WORKER_TERMINAL (vacant roster = true). */
export function workersAreTerminal(execution: Execution): boolean {
  return execution.runs
    .filter((r) => r.kind !== "captain")
    .every((r) => WORKER_TERMINAL.has(r.status));
}

/** Roles still outstanding while CEO is in ``coordination_wait``. */
export function waitingWorkerRoles(execution: Execution): string[] {
  return coordinationWaitWorkerRows(execution)
    .filter((w) => w.status !== "completed")
    .map((w) => w.role);
}

export type CoordinationWaitWorkerRow = {
  runId: string;
  role: string;
  status: RunStatus;
  summary: string;
  /** 证人席位根：pending 显示「待命」，skipped 显示「未传唤」。 */
  witnessSeat?: boolean;
};

/** All non-captain workers with display role + live run status. */
export function coordinationWaitWorkerRows(
  execution: Execution,
): CoordinationWaitWorkerRow[] {
  return execution.runs
    .filter((r) => r.kind !== "captain")
    .map((r) => ({
      runId: r.id,
      role:
        execution.agents.find((a) => a.id === r.agentId)?.role ??
        r.role ??
        r.id,
      status: r.status,
      summary: (r.outputSummary ?? "").trim(),
      witnessSeat: r.group === "debate:witness" && r.continuesRunId == null,
    }));
}

/**
 * Live ``coordination_wait`` copy for StatusStrip (long form).
 * Global only — member names stay on graph worker nodes / captain short caption.
 * ``waitingRoles`` kept in opts for call-site compat; Strip no longer embeds them.
 * No elapsed suffix: strip already shows turn 用时; wait-segment clocks were wrong.
 */
export function coordinationWaitLabel(
  wait: Pick<CoordinationWaitPayload, "completed" | "total"> | null | undefined,
  opts?: {
    /** @deprecated Strip uses global copy only; ignored. */
    waitingRoles?: string[];
  },
): string | null {
  void opts;
  if (!wait) return null;
  const total = Math.max(0, wait.total);
  const completed = Math.max(
    0,
    Math.min(wait.completed, total || wait.completed),
  );
  return `等待团队成员完成 (${completed}/${total})…`;
}

/** Short captain-node caption: 等谁 (n/m). No 已等秒数 — duplicates strip 用时. */
export function coordinationWaitCaptainCaption(
  wait: Pick<CoordinationWaitPayload, "completed" | "total"> | null | undefined,
  opts?: {
    waitingRoles?: string[];
  },
): string | null {
  if (!wait) return null;
  const total = Math.max(0, wait.total);
  const completed = Math.max(
    0,
    Math.min(wait.completed, total || wait.completed),
  );
  const roles = (opts?.waitingRoles ?? []).filter(Boolean);
  if (roles.length === 1) {
    return `等待「${roles[0]}」(${completed}/${total})`;
  }
  return `等待团队 (${completed}/${total})`;
}

/**
 * All workers finished while execution is still running — CEO writing the
 * same-turn close. Matches {@link deriveCaptainStatus}'s "running" sink.
 *
 * ``detached``: captain already left; background settle does not write a close.
 * ``turnTerminal`` without detach is still the same-turn writing window
 * (attach grace). Hiding the spinner while attached painted a false「已汇总」.
 *
 * ``paused``: cold ask / plan_review hang — workers may be 2/2, but CEO is
 * waiting on the user (same invariant as deriveCaptainStatus).
 */
export function isTeamSynthesizing(
  execution: Execution,
  opts?: { turnTerminal?: boolean; detached?: boolean },
): boolean {
  void opts?.turnTerminal;
  if (opts?.detached) return false;
  if (execution.status === "paused") return false;
  if (execution.status !== "running") return false;
  const { total } = workerProgress(execution);
  return total > 0 && workersAreTerminal(execution);
}

/** Deterministic strip / indicator copy for the synthesis empty window. */
export function teamSynthesisPhaseLabel(execution: Execution): string {
  const { completed, total } = workerProgress(execution);
  return `${completed}/${total} 已完成，正在收尾`;
}

/**
 * Short preview for the CEO graph node while final answer is not yet streaming
 * into the bubble (synthesis uses `team_synthesis_preview`, not content_delta).
 */
export function captainSynthesisPreviewText(
  preview: TeamSynthesisPreviewPayload | null | undefined,
): string {
  if (!preview) return "";
  const text = preview.text.trim();
  const headline = preview.headline.trim();
  if (text && text !== headline) return text;
  if (headline) return headline;
  const blurbs = preview.workers
    .filter((w) => w.status !== "pending" && w.summary)
    .map((w) => `${w.role}：${w.summary}`);
  return blurbs.join(" · ");
}
