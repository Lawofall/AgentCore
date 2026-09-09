import {
  ResolvedDecisionRecord,
  askResolvedDisplay,
} from "@/components/chat/decision";
import { Badge, DecisionCard } from "@/components/ui";
import { parseCheckpointIntent } from "@/lib/checkpointIntent";
import { notifyError } from "@/lib/toast";
import type { CheckpointUserDecision } from "@/services/checkpoint";
import type { CheckpointDisplay } from "@/stores/conversation";
import { timelineIntentionalEmpty } from "@/stores/interactions/timelineCardSlot";
import type { CheckpointIntent } from "@/types/events";
import { useState } from "react";
import { AskDecisionBody } from "./ask/AskDecisionBody";
import {
  type AskUserContent,
  collapsedAskGlance,
  displayAskReply,
  flattenAskNotes,
  useAskAnswer,
} from "./ask/AskUserFields";
import { OrganizePlanBody } from "./ask/OrganizePlanBody";

/**
 * Inline ask_user card — the CEO paused the turn to ask the user. This is the ONE
 * asking surface: a **generic clarification** card (questions / options / note).
 *
 * The interactive body lives in {@link AskUserCard}, reused by the durable 待恢复 resume
 * card (ResumePrompt) — one card, one answer model.
 *
 * 挂起即收口 (②, Phase 3): an inline ask_user card is never live-interactive anymore — a
 * CEO checkpoint finalizes the turn (its in-process resolve Future is never parked), so
 * the actionable surface is always the durable resume card. Inline, pending is
 * {@link timelineIntentionalEmpty} (CEO message body stays visible); settled →
 * resolved record card. Bag miss is handled upstream as {@link timelineMissingCard}.
 *
 * Resolved copy / icons come from the shared decision meta ({@link ASK_INTENT_META}).
 */
export function CheckpointCard({
  checkpoint,
}: {
  checkpoint: CheckpointDisplay;
}) {
  if (checkpoint.status === "resolved") {
    return <ResolvedCheckpoint checkpoint={checkpoint} />;
  }
  return timelineIntentionalEmpty();
}

/** Flatten per-question listed picks into resume `selected`. */
export function collectAskSelected(
  content: AskUserContent,
  answers: Record<string, string[]>,
): string[] {
  const out: string[] = [];
  for (const q of content.questions) {
    for (const v of answers[q.id] ?? []) {
      const t = v.trim();
      if (t) out.push(t);
    }
  }
  return out;
}

/**
 * The live, actionable ask_user card body — the single asking surface, shared by the
 * inline live card ({@link CheckpointCard}) and the durable 待恢复 resume card
 * (ResumePrompt). Settled by 提交 (→ continue) or 取消 (→ stop 硬停). Picks compose into ONE readable
 * note (答复模型 α), handed to `onSubmit`.
 *
 * 清单确认（`organize_plan`）走清单体；其余一律 {@link AskDecisionBody}。
 * 卡头是题干 / 批次标题；可见面不画「需要你拍板」和图标。真·风险审批由 ApprovalPrompt 承载（蓝）。
 */
export function AskUserCard({
  content,
  intent,
  onSubmit,
  conversationId,
}: {
  content: AskUserContent;
  intent: CheckpointIntent;
  onSubmit: (
    decision: CheckpointUserDecision,
    note: string,
    selected?: string[],
  ) => void | Promise<void>;
  /** 检查点 id（旧 decision 折叠路径已退役；保留形参兼容调用方）。 */
  disclosureKey?: string | null;
  /** Enables bind_local_folder action options on desktop. */
  conversationId?: string | null;
}) {
  const chrome = parseCheckpointIntent(intent);
  const ans = useAskAnswer(content, {
    seedAllMultiple: chrome === "organize_plan",
  });
  const [submitting, setSubmitting] = useState<CheckpointUserDecision | null>(
    null,
  );
  const busy = submitting !== null;
  const carriesSelected = chrome === "organize_plan";

  const send = (decision: CheckpointUserDecision, noteOverride?: string) => {
    if (busy) return;
    setSubmitting(decision);
    const baseSelected =
      decision === "continue" && carriesSelected
        ? collectAskSelected(content, ans.answers)
        : [];
    const selected = baseSelected;
    const freeNote = flattenAskNotes(content, ans.notes, ans.note);
    const composed =
      noteOverride !== undefined
        ? noteOverride
        : decision === "stop" || carriesSelected
          ? freeNote
          : ans.compose(chrome);
    Promise.resolve(onSubmit(decision, composed, selected)).catch((err) => {
      notifyError(err, "提交失败");
      setSubmitting(null);
    });
  };

  const onBindResolve = (composedAnswer: string) =>
    send("continue", composedAnswer);

  const shared = {
    content,
    answer: ans,
    busy,
    submitting,
    onContinue: () => send("continue"),
    onStop: () => send("stop"),
  };

  if (chrome === "organize_plan") {
    return (
      <DecisionCard
        tone="neutral"
        animate
        className="flex max-h-[min(60vh,36rem)] flex-col overflow-hidden p-0"
        data-ask-intent="organize_plan"
      >
        <OrganizePlanBody {...shared} />
      </DecisionCard>
    );
  }

  return (
    <DecisionCard
      tone="neutral"
      animate
      className="flex max-h-[min(50vh,28rem)] flex-col overflow-hidden p-0"
      data-ask-intent="decision"
    >
      <AskDecisionBody
        {...shared}
        conversationId={conversationId}
        onBindResolve={onBindResolve}
      />
    </DecisionCard>
  );
}

/** Collapsed glance: picks / short reply, never the CEO question or compose dump. */
function resolvedCollapsedSummary(checkpoint: CheckpointDisplay): string {
  return collapsedAskGlance({
    selected: checkpoint.selected,
    note: checkpoint.note,
    prompts: checkpoint.questions.map((q) => q.prompt),
  });
}

/** The settled record of an ask_user card: how it was decided, plus the user's
 * answer note. Process-row stub — not a success toast or DecisionCard.
 * 取消 / 确认 / 超时都占时间线存根；缺 decision 不猜超时。 */
function ResolvedCheckpoint({ checkpoint }: { checkpoint: CheckpointDisplay }) {
  const resolved = askResolvedDisplay(checkpoint.intent, checkpoint.decision);
  const reply = displayAskReply(checkpoint.note);

  return (
    <ResolvedDecisionRecord
      layout="toneStub"
      disclosureKey={checkpoint.id ? `${checkpoint.id}:resolved` : null}
      tone={resolved.tone}
      icon={resolved.icon}
      label={resolved.label}
      collapsedSummary={resolvedCollapsedSummary(checkpoint)}
      askIntent={checkpoint.intent}
    >
      <div className="mt-1.5 space-y-1.5">
        <p className="whitespace-pre-wrap text-sm text-foreground">
          {checkpoint.question}
        </p>
        {checkpoint.selected.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {checkpoint.selected.map((s) => (
              <Badge key={s} tone="muted" pill>
                {s}
              </Badge>
            ))}
          </div>
        )}
        {reply ? (
          <p className="whitespace-pre-wrap text-sm text-muted-foreground">
            {reply}
          </p>
        ) : null}
      </div>
    </ResolvedDecisionRecord>
  );
}
