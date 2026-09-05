import { createContext, useContext, useMemo } from "react";
import {
  type FoldState,
  applyFrame,
  finalizeFold,
  initFold,
  projectExecution,
} from "./project";
import {
  type ExecutionRuntime,
  execRuntime,
  reloadProcessOverlay,
  useExecutionStore,
} from "./store";
import type { AgentState, Execution, ExecutionPlan, RunNode } from "./types";

/**
 * The assistant message id whose team graph the current subtree renders.
 * Provided by {@link InlineTeamGraph} (inline graph) and the detail panel
 * (run-detail tab); the scoped hooks below read it so every graph view targets
 * the right message's slot — live or replayed — through one code path.
 */
export const ExecutionScopeContext = createContext<string | null>(null);

/** The in-scope message id (see {@link ExecutionScopeContext}). */
export function useExecutionScope(): string | null {
  return useContext(ExecutionScopeContext);
}

/**
 * One projected {@link Execution} per runtime snapshot, shared across every consumer
 * of the same turn. The store swaps a message's {@link ExecutionRuntime} for a NEW
 * object on every mutation (`patchExec` spreads), so the object identity IS a content
 * key: while a snapshot is unchanged all consumers (InlineTeamGraph / EscalationCards /
 * MultiAgentFileArtifacts / GraphView…) read the SAME fold — one `projectExecution` per
 * turn-frame instead of one per consumer per frame — and a superseded snapshot is GC'd
 * along with its cache entry. The playhead rides on `rt`, so scrubbing yields a new `rt`
 * and re-folds. Sharing one object also stabilizes referential equality downstream.
 */
const projectionCache = new WeakMap<ExecutionRuntime, Execution>();

/**
 * 增量投影 (流式性能): one advancing {@link FoldState} per live plan. The store swaps
 * `rt` for a new object every frame, so a from-scratch fold would re-replay the WHOLE
 * frame stream each tick → O(n²) over a long turn. Instead we keep the accumulator keyed
 * by the (stable-per-turn) plan object and advance it by ONLY the frames appended since
 * last projection — O(1) amortized per frame. A merge batch mints a new plan
 * ({@link mergePlanInto}), so its entry rebuilds from scratch once; a superseded plan is
 * GC'd with its entry.
 */
const liveFolds = new WeakMap<
  ExecutionPlan,
  { count: number; state: FoldState }
>();

/** Merge transport-only worker `tool_use_progress` (keyed by run_id) onto agents
 * and the matching running tool step in that run's process timeline. */
function overlayWorkerToolPhases(
  exec: Execution,
  rt: ExecutionRuntime,
): Execution {
  const phases = rt.workerToolPhases;
  if (Object.keys(phases).length === 0) return exec;
  let changed = false;
  const agents = exec.agents.map((a) => {
    const rid = a.currentRunId;
    if (!rid) return a;
    const live = phases[rid];
    if (!live) return a;
    changed = true;
    return {
      ...a,
      toolExecutionLive: { toolName: live.toolName, phase: live.phase },
    };
  });
  const runs = exec.runs.map((r) => {
    const live = phases[r.id];
    if (!live) return r;
    const idx = [...r.process]
      .map((s, i) => ({ s, i }))
      .reverse()
      .find(
        ({ s }) =>
          s.kind === "tool" &&
          s.status === "running" &&
          s.tool_name === live.toolName,
      )?.i;
    if (idx == null) return r;
    const step = r.process[idx];
    if (step.kind !== "tool" || step.phase === live.phase) return r;
    changed = true;
    const next = [...r.process];
    next[idx] = { ...step, phase: live.phase as typeof step.phase };
    return { ...r, process: next };
  });
  if (!changed) return exec;
  return { ...exec, agents, runs };
}

/** Overlay journaled per-run process[] so reload interleaving matches live.
 * Live runtimes keep `runProcesses` null; empty lanes are absent, not a wipe. */
function overlayRunProcesses(exec: Execution, rt: ExecutionRuntime): Execution {
  const map = reloadProcessOverlay(rt.runProcesses);
  if (!map) return exec;
  let changed = false;
  const runs = exec.runs.map((r) => {
    const process = map[r.id];
    if (!process) return r;
    changed = true;
    return { ...r, process };
  });
  return changed ? { ...exec, runs } : exec;
}

/**
 * Project a runtime snapshot to its {@link Execution} (WeakMap-cached per `rt`, so one
 * finalize per snapshot shared across all consumers of that turn-frame). The shared
 * source of truth for "what runs this turn has" — including 修订 vN revisions that are
 * synthesized from frames and are NOT in `plan.runs`. Callers that ask *outside* React
 * render (e.g. the {@link SidePanel} tab-visibility filter) use this directly so they
 * agree with `RunDetailBody`'s projected lookup; inside render, prefer the hooks below.
 */
export function projectRuntime(rt: ExecutionRuntime): Execution | null {
  if (!rt.plan) return null;
  const cached = projectionCache.get(rt);
  const base =
    cached ??
    (() => {
      const exec = computeProjection(rt, rt.plan);
      projectionCache.set(rt, exec);
      return exec;
    })();
  return overlayRunProcesses(overlayWorkerToolPhases(base, rt), rt);
}

function computeProjection(
  rt: ExecutionRuntime,
  plan: ExecutionPlan,
): Execution {
  const upto = rt.playhead ?? rt.frames.length;
  const entry = liveFolds.get(plan);
  // Scrubbing (fixed playhead) or a stale rt whose fold has already advanced PAST this
  // prefix → cold full-fold of the prefix; never rewind the shared live accumulator.
  let base: Execution;
  if (rt.playhead !== null || (entry && entry.count > upto)) {
    base = projectExecution(
      plan,
      rt.frames.slice(0, upto),
      rt.status,
      rt.debate,
      rt.debateRounds,
      rt.crossExamEnabled,
      rt.debateOpening,
    );
  } else {
    // Live tail: advance the incremental accumulator to the current frame count, applying
    // ONLY the newly-appended frames.
    const fold = entry ?? { count: 0, state: initFold(plan) };
    for (let i = fold.count; i < upto; i++)
      applyFrame(fold.state, rt.frames[i]);
    fold.count = upto;
    liveFolds.set(plan, fold);
    base = finalizeFold(
      fold.state,
      rt.status,
      rt.debate,
      rt.debateRounds,
      rt.crossExamEnabled,
      rt.debateOpening,
    );
  }
  // 证据台账 / 庭前取证是 runtime 槽位态（非 frame 折叠）：投影时挂上；收场权威优先。
  const evidenceLedger =
    rt.debate && Array.isArray(rt.debate.evidence_ledger)
      ? rt.debate.evidence_ledger
      : rt.evidenceLedger;
  return { ...base, evidenceLedger, debatePretrial: rt.debatePretrial };
}

/** Project a specific message's execution at its current playhead — live tail
 * or replay. Used where the message id is explicit (the inline graph + panel). */
export function useMessageExecution(
  messageId: string | null,
): Execution | null {
  const rt = useExecutionStore((s) =>
    messageId ? s.byId[messageId] : undefined,
  );
  return useMemo(() => (rt ? projectRuntime(rt) : null), [rt]);
}

/** Cheap fingerprint of one run's inspector inputs — stable under other runs' streams. */
export function runInspectorSig(
  execution: Execution | null,
  runId: string,
): string {
  if (!execution) return "";
  const run = execution.runs.find((r) => r.id === runId);
  if (!run) return `missing:${runId}`;
  const agent = execution.agents.find((a) => a.id === run.agentId);
  return `${execution.status}|${runProcessSig(run.process)}|${runMetaSig(run)}|${agentInspectorSig(agent)}`;
}

function chunkMeta(chunks: readonly string[]): string {
  const n = chunks.length;
  if (n === 0) return "0:0";
  return `${n}:${chunks[n - 1]?.length ?? 0}`;
}

function runProcessSig(process: RunNode["process"]): string {
  let tools = 0;
  let running = 0;
  let success = 0;
  let error = 0;
  let textChars = 0;
  for (const step of process) {
    if (step.kind === "tool") {
      tools++;
      if (step.status === "running") running++;
      else if (step.status === "success") success++;
      else error++;
    } else if (step.kind === "reasoning" || step.kind === "content") {
      textChars += step.text.length;
    }
  }
  const last = process.at(-1);
  const tail =
    last == null
      ? ""
      : last.kind === "tool"
        ? `${last.id}:${last.status}:${last.phase ?? ""}`
        : last.kind === "reasoning" || last.kind === "content"
          ? `${last.kind}:${last.text.length}`
          : last.kind;
  return `${process.length}:${tools}:${running}:${success}:${error}:${textChars}:${tail}`;
}

function runMetaSig(run: RunNode): string {
  return [
    run.status,
    run.phase ?? "",
    run.phaseTool ?? "",
    run.error ? "1" : "0",
    run.debrief ? "1" : "0",
    run.escalations.length,
    run.receivedContext.length,
    run.checkpoint?.status ?? "",
  ].join("|");
}

function agentInspectorSig(agent: AgentState | undefined): string {
  if (!agent) return "";
  const tp = agent.toolProgress;
  const te = agent.toolExecutionLive;
  return [
    agent.status,
    chunkMeta(agent.outputChunks),
    chunkMeta(agent.reasoningChunks),
    tp ? `${tp.toolName}:${tp.chars}` : "",
    te ? `${te.toolName}:${te.phase}` : "",
    agent.toolCalls.length,
  ].join("|");
}

export type MessageRunView = {
  execution: Execution;
  run: RunNode;
  agent: AgentState;
};

/**
 * Run-detail subscription: re-render only when THIS run's inspector inputs
 * change. Sibling workers' token/tool floods must not rebuild the tree.
 */
export function useMessageRun(
  messageId: string | null,
  runId: string,
): MessageRunView | null {
  const sig = useExecutionStore((s) => {
    if (!messageId) return "";
    const rt = s.byId[messageId];
    return runInspectorSig(rt ? projectRuntime(rt) : null, runId);
  });
  return useMemo(() => {
    if (!messageId || !sig) return null;
    const rt = useExecutionStore.getState().byId[messageId];
    const execution = rt ? projectRuntime(rt) : null;
    if (!execution) return null;
    const run = execution.runs.find((r) => r.id === runId);
    const agent = run
      ? (execution.agents.find((a) => a.id === run.agentId) ?? null)
      : null;
    if (!run || !agent) return null;
    return { execution, run, agent };
  }, [sig, messageId, runId]);
}

/**
 * Subscribe to one field of the in-scope message's execution runtime
 * ({@link ExecutionScopeContext}). Re-renders when that field changes or the
 * scope switches. Prefer this over reading the store directly.
 */
export function useActiveExecField<T>(
  selector: (rt: ExecutionRuntime) => T,
): T {
  const messageId = useContext(ExecutionScopeContext);
  return useExecutionStore((s) =>
    selector(
      (messageId ? s.byId[messageId] : undefined) ?? execRuntime(s, messageId),
    ),
  );
}

/**
 * The in-scope message's execution snapshot at the current playhead — live
 * while following the tail, historical while scrubbing. Reads the scope from
 * {@link ExecutionScopeContext}.
 */
export function useProjectedExecution(): Execution | null {
  return useMessageExecution(useContext(ExecutionScopeContext));
}
