/**
 * 热审批 / 委派授权 / 阶段推进卡时间线痕迹（统一时间线二期 D3 + 打磨批）：
 * pending 期间仅决策区有操作面（推进卡在 Dock），时间线
 * {@link timelineIntentionalEmpty}；resolved / orphaned 后在 required 时刻的标记槽
 * 显轻状态行。Store 里查不到 entry 是 {@link timelineMissingCard}，不要和 pending 合成一个 null。
 *
 * 多端同权（B2 · 验收 2）：这一拍是另一端点的时补一句归属——决策区的收口条几秒后就退场，
 * 这行痕迹才是回看时「不是我点的」的长期答案。判定只在本会话内成立，见
 * `InteractionEntry.settledElsewhere`；不确定时一个字都不加，绝不替用户认领。
 *
 * 同理，「本轮内都允许」批出去的范围与它此后放行了几次，也只有这行留得下来：被覆盖的调用
 * 不再弹卡（零噪音定案），痕迹不说，用户对这一轮的记忆就是「我一直在逐个把关」。计数见
 * {@link useUnaskedSinceGrant}。
 */
import {
  type ObservedToolCall,
  countUnaskedSinceGrant,
  observedCallSpine,
  turnGrantScope,
} from "@/lib/turnGrantSkips";
import { useActiveMessageProcess } from "@/stores/conversation";
import { type RunFrame, useExecutionStore } from "@/stores/execution";
import { toolLabel } from "@/stores/execution/types";
import {
  type InteractionEntry,
  useInteractionStore,
} from "@/stores/interactions";
import {
  timelineIntentionalEmpty,
  timelineMissingCard,
} from "@/stores/interactions/timelineCardSlot";
import type { ProcessStep } from "@/types/events";
import { Check, X } from "lucide-react";
import { useMemo } from "react";

function elsewhereSuffix(entry: InteractionEntry): string {
  return entry.settledElsewhere ? " · 已由另一端处理" : "";
}

/**
 * 卡是被提交回执关掉的、而结果那帧还没到：`resolution` 是空的，此时默认分支会说成
 * 「已批准 / 已授权开工」——那是替它猜。等 `*_resolved` 到了自然会换成真的那句。
 */
function outcomeUnknown(entry: InteractionEntry): boolean {
  return entry.settledByReceipt === true && !entry.resolution?.decision;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function toolCallsFromFrames(frames: readonly RunFrame[]): ObservedToolCall[] {
  const out: ObservedToolCall[] = [];
  for (const f of frames) {
    if (f.kind !== "tool_use_start") continue;
    out.push({ toolCallId: f.toolCallId, toolName: f.toolName });
  }
  return out;
}

function toolCallsFromProcess(
  process: readonly ProcessStep[],
): ObservedToolCall[] {
  const out: ObservedToolCall[] = [];
  for (const step of process) {
    if (step.kind !== "tool") continue;
    out.push({ toolCallId: step.id, toolName: step.tool_name });
  }
  return out;
}

/**
 * 本轮授权之后，同类操作有多少次没再问过用户。
 *
 * 服务端对被授权覆盖的调用直接短路，线材上没有「因授权免问」的事件，所以只能减出来：
 * 本回合观察到的调用（CEO 过程线 + 协作图 frame，含队员）减去弹过卡的。`scope` 为
 * null（不是授权类决定）时恒为 0，不做任何遍历。
 */
function useUnaskedSinceGrant({
  conversationId,
  messageId,
  grantToolCallId,
  scope,
}: {
  conversationId: string;
  messageId: string;
  grantToolCallId: string;
  scope: ReadonlySet<string> | null;
}): number {
  const frames = useExecutionStore((s) =>
    messageId ? s.byId[messageId]?.frames : undefined,
  );
  const process = useActiveMessageProcess(messageId || null);
  const interactions = useInteractionStore((s) => s.byId);
  return useMemo(() => {
    if (!scope || !grantToolCallId) return 0;
    const calls = observedCallSpine({
      processCalls: toolCallsFromProcess(process),
      frameCalls: frames ? toolCallsFromFrames(frames) : [],
      grantToolCallId,
    });
    const askedToolCallIds = new Set<string>();
    for (const e of interactions.values()) {
      if (e.kind !== "approval") continue;
      if (conversationId && e.conversationId !== conversationId) continue;
      askedToolCallIds.add(str(e.payload.tool_call_id) || e.id);
    }
    return countUnaskedSinceGrant({
      calls,
      grantToolCallId,
      scope,
      askedToolCallIds,
    });
  }, [conversationId, frames, grantToolCallId, interactions, process, scope]);
}

/** 本轮授权覆盖面的短文案（回看时得知道当初批出去的是多大范围）。 */
function grantScopeLabel(decision: string): string {
  return decision === "approve_always_files"
    ? "本轮内所有文件改动"
    : "本轮内都允许";
}

export function ApprovalTrace({
  approvalId,
  messageId = "",
}: {
  approvalId: string;
  /** 宿主回合（时间线 ctx）：据此取本回合的调用流，算「之后没再问几次」。 */
  messageId?: string;
}) {
  const entry = useInteractionStore((s) => s.byId.get(approvalId));
  const resolved =
    entry != null && entry.kind === "approval" && entry.status === "resolved";
  const toolName = str(entry?.payload.tool_name);
  const decision = resolved ? str(entry?.resolution?.decision) : "";
  const scope = useMemo(
    () => turnGrantScope(decision, toolName),
    [decision, toolName],
  );
  const unasked = useUnaskedSinceGrant({
    conversationId: entry?.conversationId ?? "",
    messageId: messageId || (entry?.messageId ?? ""),
    grantToolCallId: str(entry?.payload.tool_call_id) || approvalId,
    scope,
  });
  if (!entry || entry.kind !== "approval") {
    return timelineMissingCard({
      kind: "missing",
      processKind: "approval",
      id: approvalId,
    });
  }
  if (!resolved) return timelineIntentionalEmpty();
  const denied = decision === "deny";
  const tool = toolLabel(toolName) || toolName || "工具";
  const label = outcomeUnknown(entry)
    ? `已处理 · ${tool}`
    : denied
      ? `已拒绝 · ${tool}`
      : scope
        ? `已批准（${grantScopeLabel(decision)}）· ${tool}`
        : `已批准 · ${tool}`;
  // 被跳过的调用本身不弹卡（零噪音定案），但「有几次因此没问你」必须留得下来可查。
  const unaskedSuffix =
    scope && unasked > 0 ? ` · 此后 ${unasked} 次未再问你` : "";
  return (
    <div
      className="flex items-center gap-1.5 text-xs text-muted-foreground"
      data-testid="approval-trace"
    >
      <Check size={12} className="shrink-0" />
      <span>{`${label}${unaskedSuffix}${elsewhereSuffix(entry)}`}</span>
    </div>
  );
}

/** 阶段推进卡时间线轻锚点：历史回看显「已开辩 / 已选补充调研 / 已失效」。 */
export function StageCardTrace({ stageCardId }: { stageCardId: string }) {
  const entry = useInteractionStore((s) => s.byId.get(stageCardId));
  if (!entry || entry.kind !== "stage_card") {
    return timelineMissingCard({
      kind: "missing",
      processKind: "stage_card",
      id: stageCardId,
    });
  }
  if (entry.status === "orphaned") {
    return (
      <div
        className="flex items-center gap-1.5 text-xs text-muted-foreground"
        data-testid="stage-card-trace"
      >
        <X size={12} className="shrink-0" />
        <span>推进卡 · 已失效</span>
      </div>
    );
  }
  if (entry.status !== "resolved") return timelineIntentionalEmpty();
  const decision =
    typeof entry.resolution?.decision === "string"
      ? entry.resolution.decision
      : "";
  const label = outcomeUnknown(entry)
    ? "推进卡 · 已处理"
    : decision === "research_first"
      ? "推进卡 · 已选补充调研"
      : "推进卡 · 已开辩";
  return (
    <div
      className="flex items-center gap-1.5 text-xs text-muted-foreground"
      data-testid="stage-card-trace"
    >
      <Check size={12} className="shrink-0" />
      <span>{`${label}${elsewhereSuffix(entry)}`}</span>
    </div>
  );
}
