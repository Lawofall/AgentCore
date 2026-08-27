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
  rework: true,
  tool: true,
  team: true,
  graph_append: true,
  checkpoint: true,
  plan_review: true,
  escalation: true,
  approval: true,
  stage_card: true,
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
 * Drop the trailing content step(s) from the timeline (交付前核验回炉 content_reset):
 * the model's done-round draft failed the light verification (e.g. fabricated
 * citations), so its just-streamed reply text is discarded and rewritten. Mirrors the
 * backend `EventSink._accumulate_process` reset branch — pop ONLY trailing `content`
 * steps, keeping the preceding reasoning / tool steps (they really happened). Returns
 * the same reference when there is nothing to drop so callers can no-op.
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

/** Append the 核验回炉轻 chip after dropping discarded draft content. */
export function appendReworkStep(
  process: ProcessStep[] | undefined,
): ProcessStep[] {
  const steps = process ? [...process] : [];
  steps.push({ kind: "rework" });
  return steps;
}

/** Settled / rewritten chip copy (also used when streaming has already emitted content after rework). */
export const REWORK_LABEL_DONE = "引用/格式核验后已重写";
/** In-progress chip while finish_guard rework is still streaming with empty body. */
export const REWORK_LABEL_IN_PROGRESS = "正在按规则修订…";

/**
 * Presentational label for a `rework` chip / export line.
 * Streaming + no content step after this rework → in-progress; otherwise past-tense done.
 */
export function reworkChipLabel(
  isStreaming: boolean,
  hasContentAfter: boolean,
): string {
  return isStreaming && !hasContentAfter
    ? REWORK_LABEL_IN_PROGRESS
    : REWORK_LABEL_DONE;
}

/** Append a started tool call as a `running` step to the timeline.
 *
 * Skipped (returns the same reference so callers can no-op) for:
 * - a DELEGATED WORKER's call (`payload.run_id` set): workers share the turn's top-level
 *   tool_use stream, but their calls belong to their run node, not the captain bubble's
 *   inline timeline (统一团队时间线 = the CEO's OWN steps);
 * - an ORCHESTRATION call (delegate/debate): the `team` marker (dropped at `run_plan`)
 *   stands in its place as the collaboration graph's slot, so it makes no tool step.
 *   Mirrors the backend `EventSink._accumulate_process`. */
export function appendToolStep(
  process: ProcessStep[] | undefined,
  payload: ToolUseStartPayload,
): ProcessStep[] {
  if (payload.run_id || isMarkerStandinTool(payload.tool_name))
    return process ?? [];
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
 * Live / hydrate can leave narration on `message.content` while `process` already
 * carries `team` / markers (rAF edge, mid-run reload, stream_segments overlay).
 * Without this, ProcessTimeline's fallback renders the reply AFTER the graph —
 * the local-vs-cloud timing bug in the screenshot. Inserts before the first
 * `team` / `graph_append` so the graph stays below the CEO lead-in. No-op when
 * a content step already exists (same ref).
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
    if (steps[i].kind === "team" || steps[i].kind === "graph_append") {
      return [...steps.slice(0, i), marker, ...steps.slice(i)];
    }
  }
  return [...steps, marker];
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

/** Drop a `graph_append` anchor on the **appending** turn (旧 journal 兼容).
 * Dedupes by `execution_id` — one anchor per host graph per append turn.
 * `actId`/`actKind`/`authorizedBy` 为桌面呈现扩展（开新幕文案 / 授权角标）；
 * conformance 导出时剥离。
 *
 * 新路径改用 `run_plan.prev_execution_id`，由 InlineTeamGraph 渲染「续自」链接，
 * 不再发 `graph_append`。
 *
 * Optional `at` mirrors {@link appendTeamStep}: hydrate journal-slot insert. */
export function appendGraphAppendStep(
  process: ProcessStep[] | undefined,
  executionId: string,
  hostMessageId: string,
  addedCount: number,
  actId?: string | null,
  actKind?: string | null,
  authorizedBy?: string | null,
  at?: number,
): ProcessStep[] {
  if (!executionId || !hostMessageId) return process ?? [];
  if (hasMarker(process, "graph_append", "execution_id", executionId))
    return process ?? [];
  const steps = process ?? [];
  const step: ProcessStep & {
    act_id?: string;
    act_kind?: string;
    authorized_by?: string;
  } = {
    kind: "graph_append",
    execution_id: executionId,
    host_message_id: hostMessageId,
    added_count: Math.max(0, addedCount | 0),
  };
  if (actId) step.act_id = actId;
  if (actKind) step.act_kind = actKind;
  if (authorizedBy) step.authorized_by = authorizedBy;
  return insertStepAt(steps, step, at);
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

/** Drop a `plan_review` marker (plan-review gate) at its chronological spot; the card
 * body folds separately, keyed by `checkpointId`. No-op (same ref) if already present. */
export function appendPlanReviewStep(
  process: ProcessStep[] | undefined,
  checkpointId: string,
): ProcessStep[] {
  if (!checkpointId) return process ?? [];
  if (hasMarker(process, "plan_review", "checkpoint_id", checkpointId))
    return process ?? [];
  return [
    ...(process ?? []),
    { kind: "plan_review", checkpoint_id: checkpointId },
  ];
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

/** Drop a `stage_card` marker (阶段推进卡痕迹锚点；行渲染由 resolved/orphaned 门控). */
export function appendStageCardStep(
  process: ProcessStep[] | undefined,
  stageCardId: string,
): ProcessStep[] {
  if (!stageCardId) return process ?? [];
  if (hasMarker(process, "stage_card", "stage_card_id", stageCardId))
    return process ?? [];
  return [
    ...(process ?? []),
    { kind: "stage_card", stage_card_id: stageCardId },
  ];
}

/** A tool step (narrowed from {@link ProcessStep}). */
export type ToolStep = Extract<ProcessStep, { kind: "tool" }>;

/**
 * A render node for the inline timeline after consecutive tool steps are coalesced
 * (前端UX设计.md §一B). `reasoning` / `content` and the positional markers (`team` /
 * `checkpoint` / `ask` / `plan_review`) stay 1:1 with their steps — they are the
 * natural boundaries that break a tool run → 保序; a maximal run of ≥2 adjacent tool
 * steps folds into one collapsible `tool-group`; a lone tool stays inline as `tool`
 * (阈值 ≥2 — 单个不套壳，维持现状平铺).
 */
export type TimelineNode =
  | Exclude<ProcessStep, { kind: "tool" }>
  | { kind: "tool"; step: ToolStep }
  | { kind: "tool-group"; tools: ToolStep[] };

/**
 * CEO 协调空转工具（`wait`）：无用户可见副作用，只确认继续听团。
 * 过程线降噪用——这类工具步及其紧邻 reasoning 默认不对用户逐段展开。
 */
export const COORDINATION_IDLE_TOOLS: ReadonlySet<string> = new Set(["wait"]);

/** Whether a tool is coordination-idle chrome (see {@link COORDINATION_IDLE_TOOLS}). */
export function isCoordinationIdleTool(toolName: string): boolean {
  return COORDINATION_IDLE_TOOLS.has(toolName);
}

/**
 * True when this `reasoning` step belongs to a pure coordination-wait round:
 * the following contiguous tools (until the next non-tool boundary) are only
 * `wait`, OR no tools have arrived yet but the preceding contiguous tools were
 * only `wait` (live trailing thought mid wait-loop).
 *
 * Reliable on the frontend from existing `process[]` — no backend marker needed.
 */
export function isWaitIdleReasoning(
  process: ProcessStep[],
  index: number,
): boolean {
  if (process[index]?.kind !== "reasoning") return false;
  let i = index + 1;
  let sawWait = false;
  while (i < process.length) {
    const s = process[i];
    if (s.kind === "tool") {
      if (!isCoordinationIdleTool(s.tool_name)) return false;
      sawWait = true;
      i++;
      continue;
    }
    break;
  }
  if (sawWait) return true;
  return precedingToolsAreOnlyWait(process, index);
}

function precedingToolsAreOnlyWait(
  process: ProcessStep[],
  index: number,
): boolean {
  let i = index - 1;
  let sawWait = false;
  while (i >= 0) {
    const s = process[i];
    if (s.kind === "tool") {
      if (!isCoordinationIdleTool(s.tool_name)) return false;
      sawWait = true;
      i--;
      continue;
    }
    break;
  }
  return sawWait;
}

/**
 * View-layer omit: drop `wait` tool steps and wait-idle reasoning.
 * Retained for non-bubble callers; CEO bubble ProcessTimeline no longer applies
 * this by default (wait rows stay visible under collapseProcessSteps).
 * Does not mutate fold / journal. Returns the same reference when nothing removed.
 */
export function omitCoordinationIdleSteps(
  process: ProcessStep[],
): ProcessStep[] {
  if (process.length === 0) return process;
  let changed = false;
  const out: ProcessStep[] = [];
  for (let i = 0; i < process.length; i++) {
    const s = process[i];
    if (s.kind === "tool" && isCoordinationIdleTool(s.tool_name)) {
      changed = true;
      continue;
    }
    if (s.kind === "reasoning" && isWaitIdleReasoning(process, i)) {
      changed = true;
      continue;
    }
    out.push(s);
  }
  return changed ? out : process;
}

/**
 * Stable render keys for timeline nodes (时间线一期 · 流式 key 稳定化).
 *
 * Index-based keys break when markers are inserted mid-array
 * → every later node's index shifts → React unmounts/remounts
 * them (flicker,
 * lost disclosure state). Identity-bearing nodes key by their own id; text nodes
 * (`reasoning`/`content`/`rework`) key by same-kind ordinal — marker insertion never
 * disturbs the relative order of same-kind text steps, so ordinals stay stable.
 */
export function timelineNodeKeys(nodes: TimelineNode[]): string[] {
  const ordinals = new Map<string, number>();
  const ordinalKey = (kind: "reasoning" | "content" | "rework") => {
    const n = (ordinals.get(kind) ?? 0) + 1;
    ordinals.set(kind, n);
    return `${kind}-${n}`;
  };
  return nodes.map((node) => {
    switch (node.kind) {
      case "team":
        return `team-${node.execution_id}`;
      case "graph_append":
        return `gappend-${node.execution_id}-${node.host_message_id}`;
      case "checkpoint":
        return `cp-${node.checkpoint_id}`;
      case "user_interjection":
        return `inj-${node.interjection_id}`;
      case "plan_review":
        return `pr-${node.checkpoint_id}`;
      case "escalation":
        return `esc-${node.escalation_id}`;
      case "approval":
        return `appr-${node.approval_id}`;
      case "stage_card":
        return `sc-${node.stage_card_id}`;
      case "tool":
        return `tool-${node.step.id}`;
      case "tool-group":
        // A group only ever GROWS by appending adjacent tools; its first tool is
        // its stable identity.
        return `tgrp-${node.tools[0]?.id ?? "empty"}`;
      case "reasoning":
      case "content":
      case "rework":
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
 * markers `team`/`checkpoint`/`ask`/`plan_review`) passes through unchanged as a
 * boundary that breaks runs — so the true chronological order is fully preserved
 * (前端UX设计.md §一B): the team graph and the interaction cards render at their own
 * marker's slot, not stamped at the bottom. Pure & view-only: `process[]` itself is
 * untouched, so the backend / journal / conformance oracle are unaffected.
 *
 * The trailing content step (the final answer) is a `content` node, never a tool —
 * the answer can never be hidden inside a collapsed group.
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
    // Old journals may still carry retired `{kind:"ask"}` / `{kind:"team_preview"}`.
    const retired = (step as { kind: string }).kind;
    if (retired === "ask" || retired === "team_preview") continue;
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
