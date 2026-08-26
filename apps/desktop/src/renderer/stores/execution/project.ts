import { channelRedirectFace } from "@/lib/channelRedirect";
import {
  appendContentStep,
  appendReasoningStep,
  appendReworkStep,
  dropTrailingContentSteps,
  hasClosedBlockWithText,
  openBlockText,
  replaceTrailingContentStep,
  replaceTrailingReasoningStep,
} from "@/lib/processTimeline";
import type { DebateNarrativeRound, DebateResultPayload } from "@/types/events";
import type { RunFrame } from "./frames";
import {
  type AgentState,
  type BatchMetricsSnapshot,
  type Execution,
  type ExecutionPlan,
  type ExecutionStatus,
  type RunNode,
  type TeamNote,
  toolLabel,
} from "./types";

/**
 * The mutable accumulator a frame stream folds into — the "current state" the
 * graph has after the frames applied so far.
 *
 * 增量投影 (流式性能): the fold is expressed as {@link initFold} → {@link applyFrame}
 * (per frame) → {@link finalizeFold}. A from-scratch projection ({@link
 * projectExecution}) runs all three; the live store keeps ONE `FoldState` per turn
 * and advances it by a single {@link applyFrame} per new frame — so a streaming turn
 * costs O(1) amortized per token instead of re-folding the whole history each tick.
 *
 * `agentIndex` / `runIndex` map id → the SAME object held in `agents` / `runs` (not a
 * copy), so an in-place mutation through the index is visible in the array — replacing
 * the old O(n) `.find` per lookup with O(1). Only `agents` / `runs` are rendered;
 * `checkpointSteps` / `batches` / `teamNotes` are fold bookkeeping + turn-level output.
 */
export interface FoldState {
  plan: ExecutionPlan;
  agents: AgentState[];
  runs: RunNode[];
  agentIndex: Map<string, AgentState>;
  runIndex: Map<string, RunNode>;
  // plan_review_resolved carries only the checkpoint id, so remember which step
  // run ids each pause gated on (from its _required frame) to apply the decision.
  checkpointSteps: Map<string, string[]>;
  // 调度埋点量化 (深层诊断指标): WaveScheduler snapshots fold here in fire order, one per
  // delegate segment (a checkpoint / scope yield + resume appends another).
  batches: BatchMetricsSnapshot[];
  // 团队便签墙 (§2.2 通): notes workers broadcast to siblings, in post order (deduped by noteId).
  teamNotes: TeamNote[];
}

function agentFromPlan(plan: ExecutionPlan, id: string): AgentState | null {
  const spec = plan.agents.find((a) => a.id === id);
  if (!spec) return null;
  return {
    id: spec.id,
    role: spec.role,
    thinking: spec.thinking ?? true,
    status: "idle",
    currentRunId: null,
    outputChunks: [],
    reasoningChunks: [],
    toolCalls: [],
    toolProgress: null,
    toolExecutionLive: null,
  };
}

function runFromPlan(plan: ExecutionPlan, id: string): RunNode | null {
  const spec = plan.runs.find((s) => s.id === id);
  if (!spec) return null;
  return {
    id: spec.id,
    agentId: spec.agentId,
    task: spec.task,
    status: "pending",
    dependsOn: spec.dependsOn,
    outputSummary: null,
    outputFiles: [],
    debrief: null,
    durationMs: null,
    startedAt: null,
    error: null,
    failureKind: null,
    productLanded: null,
    parentRunId: spec.parentRunId ?? null,
    kind: spec.kind ?? "agent",
    role: null,
    model: null,
    usage: null,
    cost: null,
    stance: spec.stance ?? null,
    group: spec.group ?? null,
    round: spec.round ?? 0,
    sideKey: null,
    continuesRunId: null,
    continuationIndex: 0,
    revised: null,
    replacesRunId: spec.replacesRunId ?? null,
    actId: spec.actId || "act-1",
    delegateBatch: spec.delegateBatch,
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
  };
}

/**
 * attach 增量重放的帧级替换（`run_*_delta.replace`）应用到一路 chunks：`openText` 是
 * 该路末尾未闭合块的旧全文（块边界以 run 的 `process` 为准），裁掉它再接上新全文。
 *
 * chunks 只被 `.join("")` 消费（全文 / 尾部预览 / 字数），块结构由 `process` 承载，
 * 所以这里合并成「已闭合前缀 + 开放块」两片即可，两者折完保持一致。
 */
function chunksWithOpenBlockReplaced(
  chunks: string[],
  openText: string,
  text: string,
): string[] {
  const joined = chunks.join("");
  const settled = joined.slice(0, joined.length - openText.length);
  return settled ? [settled, text] : [text];
}

function addAgent(s: FoldState, a: AgentState): void {
  s.agents.push(a);
  s.agentIndex.set(a.id, a);
}

function addRun(s: FoldState, r: RunNode): void {
  s.runs.push(r);
  s.runIndex.set(r.id, r);
}

function ensureAgent(s: FoldState, id: string): void {
  if (!s.agentIndex.has(id)) {
    const a = agentFromPlan(s.plan, id);
    if (a) addAgent(s, a);
  }
}

function ensureRun(s: FoldState, id: string): void {
  if (!s.runIndex.has(id)) {
    const r = runFromPlan(s.plan, id);
    if (r) addRun(s, r);
  }
}

/** A fresh accumulator for `plan`, before any frame is applied. */
export function initFold(plan: ExecutionPlan): FoldState {
  return {
    plan,
    agents: [],
    runs: [],
    agentIndex: new Map(),
    runIndex: new Map(),
    checkpointSteps: new Map(),
    batches: [],
    teamNotes: [],
  };
}

/**
 * Fold ONE run-level fact into the accumulator, in place. Deterministic and
 * order-dependent (frames replay in stream order): applying `frames[0..n]` in
 * sequence yields the exact state the graph had after the n-th fact, which is
 * what powers both the live tail and timeline replay.
 */
export function applyFrame(s: FoldState, f: RunFrame): void {
  switch (f.kind) {
    case "run_started": {
      let run = s.runIndex.get(f.runId);
      const continuesRoot = f.continuesRunId ?? null;
      // 计划内节点优先从 plan 物化，避免「计划内续派」被误当成未入 plan 的合成续写
      //（否则会继承现场根的 task / 丢 depends_on）。
      if (!run) ensureRun(s, f.runId);
      run = s.runIndex.get(f.runId);
      // 同人接续：未入 plan 的续写（热修 / 辩论 continue_run）由本 frame 出生。
      // 身份继承自现场根（continuesRunId），parentRunId 只承载真实委派父。
      if (!run && continuesRoot) {
        ensureRun(s, continuesRoot);
        const original = s.runIndex.get(continuesRoot);
        if (original) {
          ensureAgent(s, original.agentId);
          const originAgent = s.agentIndex.get(original.agentId);
          addAgent(s, {
            id: f.agentId,
            role: originAgent?.role ?? original.agentId,
            thinking: originAgent?.thinking ?? true,
            status: "idle",
            currentRunId: null,
            outputChunks: [],
            reasoningChunks: [],
            toolCalls: [],
            toolProgress: null,
            toolExecutionLive: null,
          });
          let maxIdx = 0;
          for (const r of s.runs) {
            if (r.continuesRunId === continuesRoot) {
              maxIdx = Math.max(maxIdx, r.continuationIndex);
            }
          }
          run = {
            id: f.runId,
            agentId: f.agentId,
            task: original.task,
            status: "pending",
            dependsOn: [],
            outputSummary: null,
            outputFiles: [],
            debrief: null,
            durationMs: null,
            startedAt: null,
            error: null,
            failureKind: null,
            productLanded: null,
            parentRunId: f.parentRunId,
            kind: f.runKind,
            role: null,
            model: null,
            usage: null,
            cost: null,
            // 乙 wire 携 round/stance/side_key (单一轮次投影): debate 续写从 frame wire 读取。
            stance: f.stance ?? null,
            group: f.group ?? null,
            round: f.round ?? 0,
            sideKey: f.sideKey ?? null,
            continuesRunId: continuesRoot,
            continuationIndex: maxIdx + 1,
            revised: null,
            replacesRunId: null,
            actId: original.actId || "act-1",
            checkpoint: null,
            receivedContext: [],
            escalations: [],
            process: [],
          };
          addRun(s, run);
        }
      }
      if (!run) ensureRun(s, f.runId);
      ensureAgent(s, f.agentId);
      run = s.runIndex.get(f.runId);
      if (run) {
        run.status = "running";
        // 真实开始时间锚点：帧的后端墙钟 `t`（live/replay 一致，journal 持久化）。进行中的
        //「执行中 · Ns」live 计时据此派生，故重挂载 / 晚看 / 刷新都不再归零。
        run.startedAt = f.t;
        // Capture the 阶段2 declaration slots onto the node so a later graph
        // can read them from the projected run (inert in 阶段1).
        run.parentRunId = f.parentRunId;
        run.kind = f.runKind;
        // 计划内续派：run 已在 plan，started 时写入接续标记。
        if (continuesRoot && run.continuesRunId == null) {
          let maxIdx = 0;
          for (const r of s.runs) {
            if (r.id === run.id) continue;
            if (r.continuesRunId === continuesRoot) {
              maxIdx = Math.max(maxIdx, r.continuationIndex);
            }
          }
          run.continuesRunId = continuesRoot;
          run.continuationIndex = maxIdx + 1;
        }
        // 冷回落接手: mid-flight `_redir` carries replaces_run_id on the wire.
        if (f.replacesRunId) run.replacesRunId = f.replacesRunId;
        if (f.sideKey) run.sideKey = f.sideKey;
      }
      const agent = s.agentIndex.get(f.agentId);
      if (agent) {
        agent.status = "working";
        agent.currentRunId = f.runId;
        agent.toolProgress = null;
      }
      break;
    }
    case "run_context": {
      // 收到的上下文 (上下文传递可视化): record the structured context this run was
      // fed onto its node, so the detail panel shows exactly what the LLM saw.
      // The captain's own context is TURN-LEVEL (the message bubble, not a node):
      // skip it here so a multi-agent journal replay doesn't paint the CEO node.
      const run = s.runIndex.get(f.runId);
      if (run && run.kind !== "captain") run.receivedContext = f.blocks;
      break;
    }
    case "run_output_delta": {
      const agent = s.agentIndex.get(f.agentId);
      const runId = f.runId || agent?.currentRunId;
      const run = runId ? s.runIndex.get(runId) : undefined;
      if (f.delta && hasClosedBlockWithText(run?.process, "content", f.delta)) {
        break;
      }
      // attach 增量重放：本帧带的是这一路末尾未闭合块的全文（那一步还没说完），整块换掉；
      // 前面已闭合的步骤不动。CEO 侧同语义见 `foldContentDelta`。
      if (f.replace && f.delta) {
        const open = openBlockText(run?.process, "content");
        if (agent) {
          agent.outputChunks = chunksWithOpenBlockReplaced(
            agent.outputChunks,
            open,
            f.delta,
          );
        }
        if (run) run.process = replaceTrailingContentStep(run.process, f.delta);
        break;
      }
      if (agent) agent.outputChunks.push(f.delta);
      if (run && f.delta) {
        run.process = appendContentStep(run.process, f.delta);
      }
      break;
    }
    case "run_output_reset": {
      // 草稿丢弃的 worker 对偶（content_reset 之于 CEO）：引擎丢弃卡片已流式的草稿。清这个
      // agent 已累积的产出（重写版从干净态重累积），reasoning 是真实过程、保留。仅
      // reason=finish_guard（交付前核验回炉）折 rework chip + didRework；retry / narration
      // 等基础设施/正常流程信号不留痕（误报根治）——镜像后端 oracle 与 mobile fold
      // （conformance pins them equal）。
      const agent = s.agentIndex.get(f.agentId);
      const isFinishGuard = f.reason === "finish_guard";
      if (agent) {
        agent.outputChunks = [];
        if (isFinishGuard) agent.didRework = true;
      }
      const runId = f.runId || agent?.currentRunId;
      const run = runId ? s.runIndex.get(runId) : undefined;
      if (run) {
        const cleared = dropTrailingContentSteps(run.process);
        run.process = isFinishGuard ? appendReworkStep(cleared) : cleared;
      }
      break;
    }
    case "run_reasoning_delta": {
      const agent = s.agentIndex.get(f.agentId);
      const runId = f.runId || agent?.currentRunId;
      const run = runId ? s.runIndex.get(runId) : undefined;
      if (
        f.delta &&
        hasClosedBlockWithText(run?.process, "reasoning", f.delta)
      ) {
        break;
      }
      if (f.replace && f.delta) {
        const open = openBlockText(run?.process, "reasoning");
        if (agent) {
          agent.reasoningChunks = chunksWithOpenBlockReplaced(
            agent.reasoningChunks,
            open,
            f.delta,
          );
        }
        if (run) {
          run.process = replaceTrailingReasoningStep(run.process, f.delta);
        }
        break;
      }
      if (agent) agent.reasoningChunks.push(f.delta);
      if (run && f.delta) {
        run.process = appendReasoningStep(run.process, f.delta);
      }
      break;
    }
    case "run_tool_progress": {
      // The worker is composing a tool call's arguments (the file body for
      // file_write, …): light up the live「正在生成」line. Cleared when the call
      // starts executing (tool_use_start) or the run ends.
      const agent = s.agentIndex.get(f.agentId);
      if (agent) agent.toolProgress = { toolName: f.toolName, chars: f.chars };
      break;
    }
    case "run_phase": {
      // Worker mid-flight activity phase. winding_down is sticky over thinking/tool
      // until a terminal frame clears it (mirrors backend oracle).
      ensureRun(s, f.runId);
      const run = s.runIndex.get(f.runId);
      if (!run) break;
      if (
        run.phase === "winding_down" &&
        (f.phase === "thinking" || f.phase === "tool")
      ) {
        break;
      }
      run.phase = f.phase;
      run.phaseTool = f.phase === "tool" ? (f.toolName ?? null) : null;
      break;
    }
    case "run_completed": {
      const run = s.runIndex.get(f.runId);
      if (run) {
        run.status = "completed";
        run.outputSummary = f.outputSummary;
        run.outputFiles = f.outputFiles ?? [];
        run.debrief = f.debrief ?? null;
        run.durationMs = f.durationMs;
        // Light up this run's payroll row (§7.3B); absent on cost-less frames.
        run.role = f.role ?? null;
        run.model = f.model ?? null;
        run.usage = f.usage ?? null;
        run.cost = f.cost ?? null;
        run.phase = null;
        run.phaseTool = null;
      }
      const agent = s.agentIndex.get(f.agentId);
      if (agent) {
        agent.status = "completed";
        agent.currentRunId = null;
        agent.toolProgress = null;
      }
      break;
    }
    case "plan_review_required": {
      // 结构化挂起 2a: the scheduler paused after these step(s) completed; mark
      // them pending so the node shows a「待放行」badge.
      s.checkpointSteps.set(f.checkpointId, f.runIds);
      for (const id of f.runIds) {
        ensureRun(s, id);
        const run = s.runIndex.get(id);
        if (run) run.checkpoint = { status: "pending", decision: null };
      }
      break;
    }
    case "plan_review_resolved": {
      for (const id of s.checkpointSteps.get(f.checkpointId) ?? []) {
        const run = s.runIndex.get(id);
        if (run) {
          run.checkpoint = { status: "resolved", decision: f.decision };
        }
      }
      break;
    }
    case "plan_revised": {
      // 「计划已调整」轻痕迹 (设计 §7.2): the CEO autonomously re-bound / re-steered the
      // paused plan via replan. Tag each affected node so it paints a non-interrupting
      // trace (bind=据上游证据定稿待绑定步骤; steer=偏离后操舵未跑步骤). bind wins over steer
      // if a node is both. A stray run_id (not on this graph) is ignored.
      // Oracle declares every run in the run_plan before plan_revised fires — materialize
      // the full plan slice so late-bound nodes exist to tag (r2/r3 while still pending).
      for (const spec of s.plan.runs) ensureRun(s, spec.id);
      for (const rev of f.revisions) {
        const run = s.runIndex.get(rev.runId);
        if (run && !(run.revised === "bind" && rev.revisionKind === "steer")) {
          run.revised = rev.revisionKind;
        }
      }
      break;
    }
    case "run_failed": {
      // Plan-declared nodes may fail before run_started (e.g. continue_from rejected).
      ensureRun(s, f.runId);
      const run = s.runIndex.get(f.runId);
      if (run) {
        run.status = "failed";
        run.error = f.error;
        run.failureKind = f.failureKind ?? null;
        run.productLanded = f.productLanded ?? null;
        run.errorCode = f.errorCode ?? null;
        run.retryable = f.retryable ?? null;
        run.retryAfter = f.retryAfter ?? null;
        run.debrief = f.debrief ?? null;
        run.phase = null;
        run.phaseTool = null;
      }
      const agent = s.agentIndex.get(f.agentId);
      if (agent) {
        agent.status = "error";
        agent.toolProgress = null;
      }
      break;
    }
    case "run_cancelled": {
      // 跑一半改方向 / 整轮停止: interrupt mid-flight (orthogonal to run_failed).
      // Clear currentRunId + toolProgress so the node leaves its live「正在生成」line.
      const run = s.runIndex.get(f.runId);
      if (run) {
        run.status = "cancelled";
        run.phase = null;
        run.phaseTool = null;
      }
      const agent = s.agentIndex.get(f.agentId);
      if (agent) {
        agent.status = "cancelled";
        agent.currentRunId = null;
        agent.toolProgress = null;
      }
      break;
    }
    case "run_skipped": {
      // 级联跳过 / graceful abort: node never ran —「未执行」. Materialize from plan
      // (never got run_started) then mark skipped; agent stays idle.
      ensureRun(s, f.runId);
      const run = s.runIndex.get(f.runId);
      if (run) {
        run.status = "skipped";
        run.phase = null;
        run.phaseTool = null;
      }
      break;
    }
    case "run_progress": {
      // Progress is derived from run states below so it stays correct and
      // cumulative across multiple delegate batches (the per-batch wire
      // counters would reset). The frame is kept only as a timeline marker.
      break;
    }
    case "batch_metrics": {
      // 调度埋点量化 (深层诊断指标): accrue the scheduler snapshot for 诊断模式 (run
      // detail's 调度 block). Append per segment so a multi-batch / resumed turn keeps each.
      s.batches.push(f.metrics);
      break;
    }
    case "team_note_posted": {
      // 团队便签墙 (§2.2 通): a worker broadcast a one-line decision / heads-up to its
      // concurrent siblings — accrue TURN-LEVEL (not onto a node), in post order, deduped
      // by noteId for replay safety. Mirrors the backend oracle + mobile fold.
      if (!s.teamNotes.some((n) => n.noteId === f.noteId)) {
        s.teamNotes.push({
          noteId: f.noteId,
          runId: f.runId,
          agentId: f.agentId,
          role: f.role,
          kind: f.noteKind,
          text: f.text,
          ts: f.ts,
          status: "active",
          supersedes: f.supersedes,
          source: f.source,
        });
      }
      // 便签会过期 → supersession (§2.2): an amendment (carries `supersedes`) marks its TARGET
      // superseded (改写) / voided (作废) — `supersedeMode` is the shared discriminator. The
      // target was posted earlier so it is already in the list (frames replay in order).
      if (f.supersedes) {
        const target = s.teamNotes.find((n) => n.noteId === f.supersedes);
        if (target) {
          target.status = f.supersedeMode === "void" ? "voided" : "superseded";
        }
      }
      break;
    }
    case "run_escalation": {
      // 升级实时可见 (非阻塞): a worker flagged a decision/blocker for the CEO — append it
      // to its run so the node shows a ⚠️ badge and the card raises a live notice the
      // instant it fires. escalationId → RunEscalation.id（桌面本地；ProjectedTurn 不加 id）。
      // source → RunEscalation.source（桌面本地；conformanceFold 勿带出）。
      const run = s.runIndex.get(f.runId);
      if (run)
        run.escalations.push({
          id: f.escalationId || null,
          question: f.question,
          assumption: f.assumption,
          blocking: f.blocking,
          status: "raised",
          answer: null,
          kind: f.escalationKind,
          // 非阻塞 banner 无应答卡，故无结构化选项。
          questions: [],
          ...(f.source ? { source: f.source } : {}),
        });
      break;
    }
    case "escalation_required": {
      // 阻塞式求决策: a worker SUSPENDED on a blocking escalate — append a `pending` card.
      // awaiting=ceo → 等主管仲裁（不可答）；缺省 → 经典可答卡。
      // browserLogin → EscalationCard「需要你登录」+ 打开直播 CTA。
      const run = s.runIndex.get(f.runId);
      if (run)
        run.escalations.push({
          id: f.escalationId,
          question: f.question,
          assumption: f.assumption,
          blocking: true,
          status: "pending",
          answer: null,
          kind: f.escalationKind,
          questions: f.questions ?? [],
          ...(f.awaiting === "ceo" ? { awaiting: "ceo" as const } : {}),
          ...(f.browserLogin ? { browserLogin: true as const } : {}),
          ...(f.ownershipPaths && f.ownershipPaths.length > 0
            ? { ownershipPaths: f.ownershipPaths }
            : {}),
          ...(f.lockOwnerRunId ? { lockOwnerRunId: f.lockOwnerRunId } : {}),
          ...(f.timeoutSeconds ? { timeoutSeconds: f.timeoutSeconds } : {}),
        });
      break;
    }
    case "escalation_resolved": {
      // Settlement: flip the matching card by escalation_id (never "first pending").
      const run = s.runIndex.get(f.runId);
      const esc = run?.escalations.find(
        (e) => e.id === f.escalationId && e.status === "pending",
      );
      if (esc) {
        if (f.status === "resolved") {
          esc.status = "resolved";
          esc.answer = f.answer;
        } else if (f.status === "assumed") {
          esc.status = "assumed";
          esc.answer = null;
        } else {
          esc.status = "timed_out";
          esc.answer = null;
        }
        if (f.arbitrated_by === "ceo") {
          esc.arbitrated_by = "ceo";
          if (f.via_user != null) esc.via_user = f.via_user;
        }
      }
      break;
    }
    case "tool_use_start": {
      // A delegated worker tags its calls with `runId`, so file the call onto
      // THAT run's agent — with width>1 several workers run concurrently and the
      // old "first running run" heuristic mis-attributed them all to one. The
      // captain's own calls carry no runId (and an unresolvable id can't be
      // placed), so those fall back to the running-run heuristic as before.
      const owner =
        (f.runId ? s.runIndex.get(f.runId) : undefined) ??
        s.runs.find((r) => r.status === "running");
      const agent = owner ? s.agentIndex.get(owner.agentId) : undefined;
      if (agent) {
        agent.toolCalls.push({
          id: f.toolCallId,
          toolName: f.toolName,
          arguments: f.arguments,
          result: null,
          status: "running",
        });
        // The call's arguments finished assembling and it is now executing, so
        // the「正在生成」progress line gives way to this real tool-call row.
        agent.toolProgress = null;
      }
      // Worker tool → per-run process timeline (not CEO bubble).
      if (f.runId) {
        const run = s.runIndex.get(f.runId);
        if (run) {
          run.process = [
            ...run.process,
            {
              kind: "tool",
              id: f.toolCallId,
              tool_name: f.toolName,
              arguments: f.arguments ?? {},
              result: null,
              status: "running",
            },
          ];
        }
      }
      break;
    }
    case "tool_use_end": {
      for (const agent of s.agents) {
        const tc = agent.toolCalls.find((t) => t.id === f.toolCallId);
        if (tc) {
          tc.result = f.result;
          tc.display = f.display ?? null;
          tc.status = f.status;
          break;
        }
      }
      // Resolve the matching worker tool step (frame has no runId — search by call id).
      for (const run of s.runs) {
        for (let i = run.process.length - 1; i >= 0; i--) {
          const step = run.process[i];
          if (step.kind === "tool" && step.id === f.toolCallId) {
            run.process = [
              ...run.process.slice(0, i),
              {
                ...step,
                result: f.result,
                status: f.status,
                ...(f.display != null ? { display: f.display } : {}),
                ...(f.failure != null ? { failure: f.failure } : {}),
              },
              ...run.process.slice(i + 1),
            ];
            break;
          }
        }
      }
      break;
    }
  }
}

/**
 * Materialize the accumulator into a full {@link Execution} snapshot — the
 * post-loop finalization: surface plan-declared-but-untouched nodes, freeze a
 * stopped turn's in-flight nodes, derive progress, and attach the turn-level
 * debate / notes payloads.
 *
 * Pure w.r.t. `s`: it never mutates the accumulator's arrays or node objects (it
 * builds fresh output arrays and copies only the nodes it must freeze), so the
 * live store can keep advancing the SAME `FoldState` after a snapshot is taken.
 */
export function finalizeFold(
  s: FoldState,
  status: ExecutionStatus,
  debate: DebateResultPayload | null = null,
  debateRounds: DebateNarrativeRound[] = [],
  crossExamEnabled = false,
  debateOpening: string | null = null,
): Execution {
  // Plan-declared nodes not yet touched by frames stay visible as pending/idle
  // (replay playhead before their run_started) — appended after started nodes so
  // multi-batch delegate order matches the oracle (revision before later batch).
  const runs: RunNode[] = [...s.runs];
  for (const spec of s.plan.runs) {
    if (!s.runIndex.has(spec.id)) {
      const r = runFromPlan(s.plan, spec.id);
      if (r) runs.push(r);
    }
  }
  const agents: AgentState[] = [...s.agents];
  for (const spec of s.plan.agents) {
    if (!s.agentIndex.has(spec.id)) {
      const a = agentFromPlan(s.plan, spec.id);
      if (a) agents.push(a);
    }
  }

  // A stopped OR failed turn may leave in-flight nodes with no terminal run frame;
  // freeze them as cancelled so the card leaves its live state (no spinners /
  // progress bar) instead of looking like it is still running. `cancelled` is the
  // graceful stop (workers get run_cancelled); `failed` is the defensive case — a turn
  // that errors out (hard crash / lost terminal frame) with a still-running worker would
  // otherwise replay that node as a forever-spinning node on reload. Copy-on-write so
  // the accumulator's live objects are left untouched (a re-fold must not see them
  // frozen).
  const frozenTurn = status === "cancelled" || status === "failed";
  let finalRuns = frozenTurn
    ? runs.map((r) =>
        r.status === "running"
          ? {
              ...r,
              status: "cancelled" as const,
              phase: null,
              phaseTool: null,
            }
          : r,
      )
    : runs;
  const finalAgents = frozenTurn
    ? agents.map((a) =>
        a.status === "working" ? { ...a, status: "cancelled" as const } : a,
      )
    : agents;

  // Turn terminal: any plan-declared node that never got a terminal run frame
  // (old journals without run_skipped, or grant-then-end) closes as skipped —
  //「未执行」instead of forever「排队中」. Live streams emit run_skipped at wave
  // close; this is the journal-compat / defensive finalize pass. Covers
  // completed as well as cancelled/failed (cascade-skipped tails after a
  // successful upstream sibling batch).
  if (status === "completed" || status === "cancelled" || status === "failed") {
    finalRuns = finalRuns.map((r) =>
      r.status === "pending" ? { ...r, status: "skipped" as const } : r,
    );
  }

  return {
    id: s.plan.id,
    planType: s.plan.planType,
    taskSummary: s.plan.taskSummary,
    status,
    prevExecutionId: s.plan.prevExecutionId ?? null,
    agents: finalAgents,
    runs: finalRuns,
    acts:
      s.plan.acts && s.plan.acts.length > 0
        ? s.plan.acts
        : s.plan.planType === "single_agent"
          ? []
          : [
              {
                actId: "act-1",
                kind: s.plan.planType === "debate" ? "debate" : "multi_agent",
                title: null,
                anchorRunId: null,
                authorizedBy: null,
              },
            ],
    // Derived (not from run_progress): count terminal-completed nodes over the
    // cumulative run set, so multi-batch delegate progress is always correct.
    progress: {
      completed: finalRuns.filter((s) => s.status === "completed").length,
      total: finalRuns.length,
    },
    batches: s.batches,
    debate,
    debateRounds,
    crossExamEnabled,
    debateOpening,
    debatePretrial: null,
    evidenceLedger: Array.isArray(debate?.evidence_ledger)
      ? debate.evidence_ledger
      : [],
    teamNotes: s.teamNotes,
    noteWall: s.plan.noteWall === true,
  };
}

/**
 * Fold a prefix of the frame stream into a full {@link Execution} snapshot.
 *
 * Pure and deterministic: feeding `frames.slice(0, n)` yields the exact state
 * the graph had after the n-th fact, which is what powers timeline replay. This
 * is the from-scratch path (reload / scrub / conformance); the live store folds
 * the SAME {@link applyFrame} incrementally (增量投影) to stay O(1) per token.
 */
export function projectExecution(
  plan: ExecutionPlan,
  frames: RunFrame[],
  status: ExecutionStatus,
  debate: DebateResultPayload | null = null,
  debateRounds: DebateNarrativeRound[] = [],
  crossExamEnabled = false,
  debateOpening: string | null = null,
): Execution {
  const state = initFold(plan);
  for (const f of frames) applyFrame(state, f);
  return finalizeFold(
    state,
    status,
    debate,
    debateRounds,
    crossExamEnabled,
    debateOpening,
  );
}

/** Human-readable label for a frame, used by the timeline scrubber. */
export function describeFrame(frame: RunFrame, plan: ExecutionPlan): string {
  const role = (agentId: string) =>
    plan.agents.find((a) => a.id === agentId)?.role ?? agentId;
  const task = (runId: string) =>
    plan.runs.find((s) => s.id === runId)?.task ?? runId;

  switch (frame.kind) {
    case "run_started":
      return `${role(frame.agentId)} 开始 · ${task(frame.runId)}`;
    case "run_context":
      return `${task(frame.runId)} · 收到上下文`;
    case "run_output_delta":
      return `${role(frame.agentId)} 输出中…`;
    case "run_output_reset":
      // 仅核验回炉念「重写产出」；retry / narration 等清稿说「整理草稿」，不谎称重写。
      return frame.reason === "finish_guard"
        ? `${role(frame.agentId)} 重写产出…`
        : `${role(frame.agentId)} 整理草稿…`;
    case "run_reasoning_delta":
      return `${role(frame.agentId)} 思考中…`;
    case "run_tool_progress":
      return `${role(frame.agentId)} 生成 ${toolLabel(frame.toolName)}…`;
    case "run_phase": {
      const phaseText =
        frame.phase === "waiting_children"
          ? "等待子团队"
          : frame.phase === "winding_down"
            ? "收尾中"
            : frame.phase === "tool"
              ? frame.toolName
                ? `调用 ${toolLabel(frame.toolName)}`
                : "调用工具"
              : "思考中";
      return `${role(frame.agentId)} ${phaseText}`;
    }
    case "run_completed":
      return `${role(frame.agentId)} 完成`;
    case "run_failed":
      return `${role(frame.agentId)} 失败`;
    case "run_cancelled":
      if (frame.reason === "redirect") return `${role(frame.agentId)} 已改方向`;
      // 硬超时强杀 ≠ 改方向：没人给它派新活，是撞了时间上限被结束。
      if (frame.reason === "worker_timeout")
        return `${role(frame.agentId)} 超时结束`;
      return `${role(frame.agentId)} 已停止`;
    case "run_skipped":
      return frame.reason === "abort"
        ? `${role(frame.agentId)} 未执行 · 已中止`
        : `${role(frame.agentId)} 未执行`;
    case "run_progress":
      return `进度 ${frame.completed}/${frame.total}`;
    case "batch_metrics":
      return `调度快照 · ${frame.metrics.nodes} 节点 · 峰值并发 ${frame.metrics.peakRunning}`;
    case "run_escalation":
      return frame.source === "validation_thrash" ||
        frame.source === "ceiling_backstop"
        ? `${role(frame.agentId)} 卡住早停`
        : `${role(frame.agentId)} 边干边上报`;
    case "escalation_required":
      return frame.browserLogin
        ? `${role(frame.agentId)} 需要你登录`
        : `${role(frame.agentId)} 求决策 · 待你拍板`;
    case "escalation_resolved":
      if (frame.status === "resolved") {
        return `${role(frame.agentId)} 已获答复 · 继续`;
      }
      if (frame.status === "assumed") {
        return `${role(frame.agentId)} 按假设继续`;
      }
      return `${role(frame.agentId)} 超时未答 · 按假设继续`;
    case "tool_use_start":
      return `调用工具 ${frame.toolName}`;
    case "tool_use_end":
      if (frame.status === "success") return "工具完成";
      if (frame.status === "redirect") {
        const face = channelRedirectFace(frame.failure?.code);
        return face ? face.label : "改用其他工具";
      }
      return "工具失败";
    case "plan_review_required":
      return "执行暂停 · 待你放行";
    case "plan_review_resolved":
      return frame.decision === "stop"
        ? "已停止 · 未运行下游"
        : "已放行 · 继续";
    case "plan_revised": {
      const bound = frame.revisions.filter(
        (r) => r.revisionKind === "bind",
      ).length;
      const steered = frame.revisions.filter(
        (r) => r.revisionKind === "steer",
      ).length;
      const parts: string[] = [];
      if (bound) parts.push(`职责已定稿 ${bound}`);
      if (steered) parts.push(`方向已校准 ${steered}`);
      return parts.length > 0 ? parts.join(" · ") : "计划已调整";
    }
    case "team_note_posted":
      return `${frame.role || role(frame.agentId)} 贴便签`;
  }
}

/**
 * Wall-clock span covered by a frame stream, in ms (0 if fewer than 2 frames).
 *
 * Used for the completed task card's "用时" summary. Wall-clock (last − first
 * frame timestamp) is correct regardless of parallelism, unlike summing
 * per-run durations which would overcount concurrent agents.
 */
export function elapsedMs(frames: RunFrame[]): number {
  if (frames.length < 2) return 0;
  return Math.max(0, frames[frames.length - 1].t - frames[0].t);
}
