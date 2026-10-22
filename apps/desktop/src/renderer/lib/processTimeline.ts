// 单聊 process timeline 的纯 fold helpers（思考·正文·工具内联时间线，前端UX设计.md §一B）。
//
// 生产实时渲染（stores/conversation.ts 经 foldMessageLane）与跨端协议巡检
// （protocol/conformanceFold.ts）共用 processTimeline + foldMessageLane 纯函数——
// live 流、reload 回放、conformance golden 读到同一形状。
//
// 不可变：每个 append* 返回新 process 数组；resolveToolStep 在无匹配时返回原引用，便于
// store 做 no-op 短路。

import { assertNever } from "@/lib/assertNever";
import { resolveToolWireStatus } from "@/lib/channelRedirect";
import type {
  ProcessStep,
  ToolPhase,
  ToolUseEndPayload,
  ToolUseProgressPayload,
  ToolUseStartPayload,
} from "@/types/events";
import { isMarkerStandinTool } from "@agentcore/protocol-fold-kit";

/** Shared protocol tool sets — SSOT `@agentcore/protocol-fold-kit` (not a fold核). */
export {
  MARKER_STANDIN_TOOLS,
  ORCHESTRATION_TOOLS,
  isMarkerStandinTool,
  isOrchestrationTool,
} from "@agentcore/protocol-fold-kit";

/**
 * 锚 A · ProcessStep kind（编译期响）：{@link PROCESS_STEP_KIND} 是
 * `Record<ProcessStep["kind"], true>`。ProcessStep 是契约单一源穷尽联合
 * (events.generated.ts)。做成 Record → 契约加新 kind、`pnpm gen:types` 后缺键即
 * `tsc` 失败，直到登记。与 {@link timelineNodeKeys} 的 `assertNever` 同款棘轮
 * （样板：mobile `EVENT_PARITY` + 两端 fold）。
 */
export const PROCESS_STEP_KIND: Record<ProcessStep["kind"], true> = {
  reasoning: true,
  content: true,
  tool: true,
  team: true,
  checkpoint: true,
  escalation: true,
  approval: true,
  user_interjection: true,
};

/**
 * True when `text` is already a closed `kind` block and the lane is no longer
 * writing that kind (last step is a tool / marker / the other lane).
 *
 * Attach / follow catch-up resends journal `process_content` as a whole-block
 * `content_delta`. After a checkpoint or reasoning step the open block is gone,
 * so a naive replace/append would paint the same paragraph twice. Live token
 * deltas are short and do not equal a finished step.
 */
export function hasClosedBlockWithText(
  process: ProcessStep[] | undefined,
  kind: "content" | "reasoning",
  text: string,
): boolean {
  if (!text || !process?.length) return false;
  const last = process[process.length - 1];
  if (last?.kind === kind) return false;
  return process.some((s) => s.kind === kind && s.text === text);
}

/**
 * Fold one reasoning delta into the timeline: extend the trailing reasoning step
 * when the last step is thinking, else open a new one. Coalescing consecutive
 * deltas keeps the timeline a few segments (one per think→act boundary) rather
 * than one node per token.
 */
export function appendReasoningStep(
  process: ProcessStep[] | undefined,
  delta: string,
): ProcessStep[] {
  const steps = process ? [...process] : [];
  const last = steps[steps.length - 1];
  if (last && last.kind === "reasoning") {
    steps[steps.length - 1] = { ...last, text: last.text + delta };
  } else {
    if (hasClosedBlockWithText(process, "reasoning", delta))
      return process ?? [];
    steps.push({ kind: "reasoning", text: delta });
  }
  return steps;
}

/**
 * Fold one content delta into the timeline: extend the trailing content step when
 * the last step is reply text, else open a new one. The trailing content step is
 * the final answer — the timeline IS the reply (前端UX设计.md §一B).
 */
export function appendContentStep(
  process: ProcessStep[] | undefined,
  delta: string,
): ProcessStep[] {
  const steps = process ? [...process] : [];
  const last = steps[steps.length - 1];
  if (last && last.kind === "content") {
    steps[steps.length - 1] = { ...last, text: last.text + delta };
  } else {
    if (hasClosedBlockWithText(process, "content", delta)) return process ?? [];
    steps.push({ kind: "content", text: delta });
  }
  return steps;
}

/**
 * The existing full text of the timeline's trailing OPEN text block on `kind`'s lane
 * — i.e. the last step when it is a `content` / `reasoning` step of that lane, else `""`
 * (the lane's last block was closed by a tool / marker / the other lane's step).
 *
 * The block boundary the attach 增量重放 `replace` semantics talk about: the deltas that
 * folded into this step are exactly the tail of the lane's concatenated scalar.
 */
export function openBlockText(
  process: ProcessStep[] | undefined,
  kind: "content" | "reasoning",
): string {
  const last = process?.[process.length - 1];
  if (!last) return "";
  if (last.kind !== "content" && last.kind !== "reasoning") return "";
  return last.kind === kind ? last.text : "";
}

/**
 * Replace the trailing OPEN content block's whole text (attach 增量重放 `replace`):
 * the frame carries the full text of the step that is still being written, so the
 * block is swapped rather than grown. Earlier (closed) steps are untouched. Opens a
 * new content step when the lane has no open block — mirrors {@link appendContentStep}
 * for the same "last step isn't mine" case.
 */
export function replaceTrailingContentStep(
  process: ProcessStep[] | undefined,
  text: string,
): ProcessStep[] {
  const steps = process ? [...process] : [];
  const last = steps[steps.length - 1];
  if (last?.kind === "content") steps[steps.length - 1] = { ...last, text };
  else {
    if (hasClosedBlockWithText(process, "content", text)) return process ?? [];
    steps.push({ kind: "content", text });
  }
  return steps;
}

/** Reasoning-lane twin of {@link replaceTrailingContentStep}. */
export function replaceTrailingReasoningStep(
  process: ProcessStep[] | undefined,
  text: string,
): ProcessStep[] {
  const steps = process ? [...process] : [];
  const last = steps[steps.length - 1];
  if (last?.kind === "reasoning") steps[steps.length - 1] = { ...last, text };
  else {
    if (hasClosedBlockWithText(process, "reasoning", text))
      return process ?? [];
    steps.push({ kind: "reasoning", text });
  }
  return steps;
}

/**
 * Drop the trailing content step(s) from the timeline (`content_reset` /
 * `run_output_reset`): the just-streamed reply text is discarded and rewritten.
 * Mirrors the backend `EventSink._accumulate_process` reset branch — pop ONLY
 * trailing `content` steps, keeping the preceding reasoning / tool steps (they
 * really happened). Returns the same reference when there is nothing to drop so
 * callers can no-op. All reset reasons leave no process trace.
 */
export function dropTrailingContentSteps(
  process: ProcessStep[] | undefined,
): ProcessStep[] {
  if (!process || process.length === 0) return process ?? [];
  if (process[process.length - 1].kind !== "content") return process;
  const steps = [...process];
  while (steps.length > 0 && steps[steps.length - 1].kind === "content") {
    steps.pop();
  }
  return steps;
}

/** Append a started tool call as a `running` step to the timeline.
 *
 * Skipped (returns the same reference so callers can no-op) for:
 * - a DELEGATED WORKER's call (`payload.run_id` set): workers share the turn's top-level
 *   tool_use stream, but their calls belong to their run node, not the captain bubble's
 *   inline timeline (统一团队时间线 = the CEO's OWN steps);
 * - an ORCHESTRATION call (delegate/debate): the `team` marker (dropped at `run_plan`)
 *   stands in its place as the collaboration graph's slot, so it makes no tool step.
 *   Mirrors the backend `EventSink._accumulate_process`;
 * - a `tool_call_id` already on the lane: `tool_use_start` is one step, same as
 *   `team` / other markers. GET process and an incremental attach segment may each
 *   carry the same start; a second fold is not a new call. */
export function appendToolStep(
  process: ProcessStep[] | undefined,
  payload: ToolUseStartPayload,
): ProcessStep[] {
  if (payload.run_id || isMarkerStandinTool(payload.tool_name))
    return process ?? [];
  const id = payload.tool_call_id;
  if (id && process?.some((s) => s.kind === "tool" && s.id === id))
    return process;
  const steps = process ? [...process] : [];
  steps.push({
    kind: "tool",
    id: payload.tool_call_id,
    tool_name: payload.tool_name,
    arguments: payload.arguments ?? {},
    result: null,
    status: "running",
  });
  return steps;
}

/**
 * Resolve a tool step (result + status) on its matching `tool_use_end`; returns the
 * same array reference when no step matches (id absent) so callers can no-op.
 *
 * `display` / `failure` are written ONLY when the payload carries them — a
 * value-less field leaves the key ABSENT (not null), matching the backend
 * oracle's golden + EventSink (无富渲染 / 无产品失败面 → 字段不出现). The
 * renderer treats absent/null display identically; `failure` absent keeps the
 * legacy peek fallback onto model-facing `result`.
 */
export function resolveToolStep(
  process: ProcessStep[] | undefined,
  payload: ToolUseEndPayload,
): ProcessStep[] | undefined {
  // A worker's / marker-standin call never entered the captain timeline (see
  // appendToolStep) — no-op.
  if (payload.run_id || isMarkerStandinTool(payload.tool_name)) return process;
  if (!process) return process;
  let changed = false;
  const steps = process.map((s) => {
    if (!changed && s.kind === "tool" && s.id === payload.tool_call_id) {
      changed = true;
      const resolved = {
        ...s,
        result: payload.result,
        status: resolveToolWireStatus(payload.status, payload.failure),
      };
      if (payload.display != null) resolved.display = payload.display;
      if (payload.failure != null) resolved.failure = payload.failure;
      return resolved;
    }
    return s;
  });
  return changed ? steps : process;
}

/**
 * 工具执行阶段进度 (联网搜索前端展示优化): stamp a RUNNING tool step's latest coarse `phase`
 * from a `tool_use_progress` event (web_search → queued / querying / fallback), driving the
 * waiting-state text so the user sees a live, honest state instead of a bare spinner.
 *
 * LIVE-ONLY: this event never rides a journal / conformance vector, so it is folded ONLY on the
 * production stream (conformanceFold never calls this) — the golden's tool steps stay phase-less
 * and the optional `phase` field keeps every ProjectedTurn byte-identical. Writes ONLY while the
 * step is still `running` (a late phase racing after `tool_use_end` is ignored) and only for the
 * captain's OWN calls (a worker / orchestration call never entered this timeline — see
 * {@link appendToolStep}). Returns the same reference when nothing matched so callers no-op.
 */
export function resolveToolStepPhase(
  process: ProcessStep[] | undefined,
  payload: ToolUseProgressPayload,
): ProcessStep[] | undefined {
  if (payload.run_id || isMarkerStandinTool(payload.tool_name)) return process;
  if (!process) return process;
  let changed = false;
  const steps = process.map((s) => {
    if (
      !changed &&
      s.kind === "tool" &&
      s.id === payload.tool_call_id &&
      s.status === "running"
    ) {
      changed = true;
      // Wire `phase` is a widened string (forward-compat); the UI maps known ToolPhase
      // values to text and falls back to a generic label for anything else.
      return { ...s, phase: payload.phase as ToolPhase };
    }
    return s;
  });
  return changed ? steps : process;
}

/** Whether a positional marker step of `kind` keyed by `key`==`value` is already in the
 * timeline — keeps a replayed / multi-batch event from dropping a duplicate anchor. */
function hasMarker(
  process: ProcessStep[] | undefined,
  kind: ProcessStep["kind"],
  key: string,
  value: string,
): boolean {
  return !!process?.some(
    (s) => s.kind === kind && (s as Record<string, unknown>)[key] === value,
  );
}

/**
 * Promote CEO prose from the message content scalar into `process[]` when the
 * timeline has no `content` step yet (协作图时间线落点).
 *
 * Inserts before the first `team` so the graph stays below the CEO lead-in.
 * No-op when a content step already exists (same ref). Remainder of a longer
 * deliverable / in-flight channel → {@link completeProcessTranscript}.
 */
export function promoteScalarContentIntoProcess(
  process: ProcessStep[] | undefined,
  content: string,
): ProcessStep[] {
  const text = content || "";
  if (!text) return process ?? [];
  const steps = process ?? [];
  if (steps.some((s) => s.kind === "content")) return steps;
  const marker: ProcessStep = { kind: "content", text };
  for (let i = 0; i < steps.length; i++) {
    if (steps[i].kind === "team") {
      return [...steps.slice(0, i), marker, ...steps.slice(i)];
    }
  }
  return [...steps, marker];
}

/**
 * Fold leftover user-visible prose into the display block list (GET / hydrate).
 *
 * Journalled `content` steps are consumed as a prefix of `extraText`. Remainder
 * extends a trailing open content step, or appends after tools. Mirrors
 * `complete_process_transcript` on the server — bubble SoT is `process[]`.
 */
export function completeProcessTranscript(
  process: ProcessStep[] | undefined,
  extraText: string,
): ProcessStep[] {
  const text = extraText || "";
  if (!text) return process ?? [];
  const steps = process ?? [];
  if (steps.some((s) => s.kind === "content" && s.text === text)) {
    return steps;
  }
  let remaining = text;
  let lastWasConsumed = false;
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    if (step.kind !== "content" || !step.text) continue;
    if (!remaining.startsWith(step.text)) continue;
    remaining = remaining.slice(step.text.length);
    if (remaining.startsWith("\n\n")) remaining = remaining.slice(2);
    else if (remaining.startsWith("\n")) remaining = remaining.slice(1);
    if (i === steps.length - 1) lastWasConsumed = true;
    if (!remaining) return steps;
  }
  if (!remaining) return steps;
  if (lastWasConsumed) {
    const last = steps[steps.length - 1];
    if (last?.kind === "content") {
      return [...steps.slice(0, -1), { ...last, text: last.text + remaining }];
    }
  }
  if (!steps.some((s) => s.kind === "content")) {
    return promoteScalarContentIntoProcess(steps, remaining);
  }
  return [...steps, { kind: "content", text: remaining }];
}

/** Re-fold leftover `content` into `process` on a cached / overlaid assistant row. */
export function stitchAssistantProcess<
  T extends {
    role: string;
    content?: string;
    process?: ProcessStep[];
  },
>(message: T): T {
  if (message.role !== "assistant") return message;
  const text = message.content ?? "";
  if (!text) return message;
  const process = completeProcessTranscript(message.process, text);
  if (process === message.process) return message;
  return { ...message, process };
}

/** Drop a `team` marker (collaboration graph slot) at the turn's FIRST `run_plan`
 * (统一团队时间线): later same-execution batches merge into one graph, so only one marker
 * per execution. Returns the same reference when already present so callers can no-op.
 * Mirrors the backend `EventSink._accumulate_process`.
 *
 * Live path omits `at` (append — content after `run_plan` has not arrived yet).
 * Hydrate backfill passes `at` = journal-relative slot so post-plan content is not
 * pushed above the graph. */
export function appendTeamStep(
  process: ProcessStep[] | undefined,
  executionId: string,
  at?: number,
): ProcessStep[] {
  if (!executionId) return process ?? [];
  if (hasMarker(process, "team", "execution_id", executionId))
    return process ?? [];
  const steps = process ?? [];
  const marker: ProcessStep = { kind: "team", execution_id: executionId };
  return insertStepAt(steps, marker, at);
}

/** Insert `marker` at `at`, or append when `at` is omitted / past the end. */
function insertStepAt(
  steps: ProcessStep[],
  marker: ProcessStep,
  at?: number,
): ProcessStep[] {
  if (at === undefined || at >= steps.length) return [...steps, marker];
  const idx = Math.max(0, at);
  return [...steps.slice(0, idx), marker, ...steps.slice(idx)];
}

/** Drop a `checkpoint` marker (blocking ask_user) at its chronological spot; the card
 * body folds separately, keyed by `checkpointId`. No-op (same ref) if already present. */
export function appendCheckpointStep(
  process: ProcessStep[] | undefined,
  checkpointId: string,
): ProcessStep[] {
  if (!checkpointId) return process ?? [];
  if (hasMarker(process, "checkpoint", "checkpoint_id", checkpointId))
    return process ?? [];
  return [
    ...(process ?? []),
    { kind: "checkpoint", checkpoint_id: checkpointId },
  ];
}

/** Drop a `user_interjection` marker (mid-turn steer / 协调插话) at its chronological
 * spot. Zero-width positional only — body + 五态 live in
 * `execution.userInterjections` keyed by `interjectionId`. First appearance of an id
 * appends (typically `status=received`); later status updates dedupe. Optional `at`
 * mirrors {@link appendTeamStep} for journal-slot hydrate. */
export function appendUserInterjectionStep(
  process: ProcessStep[] | undefined,
  interjectionId: string,
  at?: number,
): ProcessStep[] {
  if (!interjectionId) return process ?? [];
  if (
    hasMarker(process, "user_interjection", "interjection_id", interjectionId)
  )
    return process ?? [];
  const steps = process ?? [];
  const marker: ProcessStep = {
    kind: "user_interjection",
    interjection_id: interjectionId,
  };
  return insertStepAt(steps, marker, at);
}

/** Drop an `escalation` marker (blocking required or non-blocking raised). */
export function appendEscalationStep(
  process: ProcessStep[] | undefined,
  escalationId: string,
): ProcessStep[] {
  if (!escalationId) return process ?? [];
  if (hasMarker(process, "escalation", "escalation_id", escalationId))
    return process ?? [];
  return [
    ...(process ?? []),
    { kind: "escalation", escalation_id: escalationId },
  ];
}

/** Drop an `approval` marker (热审批痕迹锚点；行渲染由 resolved 门控). */
export function appendApprovalStep(
  process: ProcessStep[] | undefined,
  approvalId: string,
): ProcessStep[] {
  if (!approvalId) return process ?? [];
  if (hasMarker(process, "approval", "approval_id", approvalId))
    return process ?? [];
  return [...(process ?? []), { kind: "approval", approval_id: approvalId }];
}

/** A tool step (narrowed from {@link ProcessStep}). */
export type ToolStep = Extract<ProcessStep, { kind: "tool" }>;

/**
 * A render node for the inline timeline after consecutive tool steps are coalesced
 * (前端UX设计.md §一B). `reasoning` / `content` and the positional markers (`team` /
 * `checkpoint`) stay 1:1 with their steps — they are the
 * natural boundaries that break a tool run → 保序; a maximal run of ≥2 adjacent tool
 * steps folds into one collapsible `tool-group`; a lone tool stays inline as `tool`
 * (阈值 ≥2 — 单个不套壳，维持现状平铺).
 */
export type TimelineNode =
  | Exclude<ProcessStep, { kind: "tool" }>
  | { kind: "tool"; step: ToolStep }
  | { kind: "tool-group"; tools: ToolStep[] };

export function estimateTimelineNodeSize(
  node: TimelineNode | undefined,
): number {
  if (!node) return 36;
  switch (node.kind) {
    case "content":
      return 72;
    case "reasoning":
      return 36;
    case "tool":
    case "tool-group":
      return 32;
    default:
      return 40;
  }
}

/**
 * Stable render keys for timeline nodes (时间线一期 · 流式 key 稳定化).
 *
 * Index-based keys break when markers are inserted mid-array
 * → every later node's index shifts → React unmounts/remounts
 * them (flicker,
 * lost disclosure state). Identity-bearing nodes key by their own id; text nodes
 * (`reasoning`/`content`) key by same-kind ordinal — marker insertion never
 * disturbs the relative order of same-kind text steps, so ordinals stay stable.
 */
export function timelineNodeKeys(nodes: TimelineNode[]): string[] {
  const ordinals = new Map<string, number>();
  const ordinalKey = (kind: "reasoning" | "content") => {
    const n = (ordinals.get(kind) ?? 0) + 1;
    ordinals.set(kind, n);
    return `${kind}-${n}`;
  };
  return nodes.map((node) => {
    switch (node.kind) {
      case "team":
        return `team-${node.execution_id}`;
      case "checkpoint":
        return `cp-${node.checkpoint_id}`;
      case "user_interjection":
        return `inj-${node.interjection_id}`;
      case "escalation":
        return `esc-${node.escalation_id}`;
      case "approval":
        return `appr-${node.approval_id}`;
      case "tool":
        return `tool-${node.step.id}`;
      case "tool-group":
        // A group only ever GROWS by appending adjacent tools; its first tool is
        // its stable identity.
        return `tgrp-${node.tools[0]?.id ?? "empty"}`;
      case "reasoning":
      case "content":
        return ordinalKey(node.kind);
      default:
        return assertNever(node);
    }
  });
}

/**
 * Coalesce a process timeline's consecutive tool steps into render nodes: a run of
 * ≥2 adjacent `kind:"tool"` steps becomes one `tool-group`, a lone tool stays an
 * inline `tool`, and every non-tool step (`reasoning`/`content` AND the positional
 * markers `team`/`checkpoint`) passes through unchanged as a
 * boundary that breaks runs — so the true chronological order is fully preserved
 * (前端UX设计.md §一B): the team graph and the interaction cards render at their own
 * marker's slot, not stamped at the bottom. Pure & view-only: `process[]` itself is
 * untouched, so the backend / journal / conformance oracle are unaffected.
 *
 * The trailing content step (the final answer) is a `content` node, never a tool —
 * the answer can never be hidden inside a collapsed group.
 *
 * CEO-bubble *process summary* collapse is a separate gate ({@link processFoldMask}):
 * reasoning / tools fold; every `content` step stays out (中间正文 ≠ 旁白).
 */
export function groupToolRuns(process: ProcessStep[]): TimelineNode[] {
  const nodes: TimelineNode[] = [];
  let run: ToolStep[] = [];
  const flush = () => {
    if (run.length === 0) return;
    nodes.push(
      run.length === 1
        ? { kind: "tool", step: run[0] }
        : { kind: "tool-group", tools: run },
    );
    run = [];
  };
  for (const step of process) {
    const kind = (step as { kind: string }).kind;
    if (!(kind in PROCESS_STEP_KIND)) continue;
    if (step.kind === "tool") {
      run.push(step);
    } else {
      flush();
      nodes.push(step as Exclude<ProcessStep, { kind: "tool" }>);
    }
  }
  flush();
  return nodes;
}

const EMPTY_PENDING_GATES: ReadonlySet<string> = new Set();

/**
 * Visual lane after fold (CEO bubble). Not a protocol change.
 *
 * - `chrome` — reasoning / tools / resolved gates (the process)
 * - `answer` — every user-visible `content` step（含中间段）
 * - `slot` — team graph / pending gate / interjection / escalation
 *
 * Collapsed summary is a caption over `answer`, not a `chrome` row.
 */
export type ProcessTurnLane = "chrome" | "answer" | "slot";

export function processTurnLane(
  node: TimelineNode,
  folded: boolean,
): ProcessTurnLane {
  switch (node.kind) {
    case "content":
      return "answer";
    case "team":
    case "user_interjection":
    case "escalation":
      return "slot";
    case "checkpoint":
      return folded ? "chrome" : "slot";
    case "reasoning":
    case "tool":
    case "tool-group":
    case "approval":
      return "chrome";
    default:
      return assertNever(node);
  }
}

/**
 * CEO 气泡完成态过程折：true = 收进「Thought · Used tools」摘要。
 * 用户可见正文（含中间段）/ 待拍板 / 图 / 插话不进折；已答复 ask 进折。
 * 纯渲染；不改 process[] / journal / conformance。
 */
export function processFoldMask(
  nodes: readonly TimelineNode[],
  pendingGateIds: ReadonlySet<string> = EMPTY_PENDING_GATES,
): boolean[] {
  return nodes.map((node) => {
    switch (node.kind) {
      case "reasoning":
      case "tool":
      case "tool-group":
      case "approval":
        return true;
      case "checkpoint":
        return !pendingGateIds.has(node.checkpoint_id);
      case "content":
        return false;
      default:
        return false;
    }
  });
}
