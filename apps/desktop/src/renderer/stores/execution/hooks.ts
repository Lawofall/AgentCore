import { createContext, useContext, useMemo } from "react";
import {
  type FoldState,
  applyFrame,
  finalizeFold,
  initFold,
  projectExecution,
} from "./project";
import { type ExecutionRuntime, execRuntime, useExecutionStore } from "./store";
import type { Execution, ExecutionPlan } from "./types";

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

/** Overlay journaled per-run process[] so reload interleaving matches live. */
function overlayRunProcesses(exec: Execution, rt: ExecutionRuntime): Execution {
  const map = rt.runProcesses;
  if (!map || Object.keys(map).length === 0) return exec;
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
