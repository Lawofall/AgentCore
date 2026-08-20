import { MANUAL_HELP, ManualHelpLink } from "@/components/ManualHelpLink";
import { escalationRowKindLabel } from "@/components/graph/agentNode/shared";
import { Button, DecisionCard, DecisionCardIcon } from "@/components/ui";
import { interactiveCheckpointTone } from "@/components/ui/tone-presets";
import { notifyError } from "@/lib/toast";
import {
  type EscalationUserDecision,
  decideEscalation,
} from "@/services/escalation";
import { notifySubmitInteractionResult } from "@/services/interactionSubmit";
import { type RunEscalation, useMessageExecution } from "@/stores/execution";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  HelpCircle,
  Loader2,
  Megaphone,
} from "lucide-react";
import { useState } from "react";
import { BrowserLoginDecisionCard } from "./BrowserLoginDecisionCard";
import {
  AskNoteField,
  AskQuestionFields,
  type AskUserContent,
  useAskAnswer,
} from "./ask/AskUserFields";
import { ResolvedDecisionRecord } from "./decision";
import { escalationWaitNote } from "./escalationWaitCopy";

/** Stable disclosure key for settled / raised escalation cards (legacy null id → role+q). */
function escalationDisclosureKey(
  escalation: RunEscalation,
  role: string,
  facet: "raised" | "resolved",
): string {
  if (escalation.id) return `escalation:${facet}:${escalation.id}`;
  return `escalation:${facet}:${role}:${escalation.question.slice(0, 80)}`;
}

function escalationKindTag(esc: RunEscalation): string | null {
  return escalationRowKindLabel(esc);
}

export function EscalationCard({
  escalation,
  role,
  conversationId,
  interactive,
}: {
  escalation: RunEscalation;
  role: string;
  conversationId: string | null;
  interactive: boolean;
}) {
  // 非阻塞上报 (run_escalation): the worker flagged a decision but kept working on its
  // assumption — a turn-level NOTICE, never a 待拍板 card (no resolve target). Handled
  // first so it never falls through to the pending path (which POSTs to a null id).
  if (escalation.status === "raised") {
    return <RaisedEscalation escalation={escalation} role={role} />;
  }
  if (
    escalation.status === "resolved" ||
    escalation.status === "assumed" ||
    escalation.status === "timed_out"
  ) {
    return <ResolvedEscalation escalation={escalation} role={role} />;
  }
  // browser_login must stay user-facing (password); check before awaiting=ceo.
  if (escalation.status === "pending" && escalation.browserLogin) {
    return (
      <PendingBrowserLoginEscalation
        escalation={escalation}
        role={role}
        conversationId={conversationId}
      />
    );
  }
  // 写权冲突：结构化「移交写权 / 保持原主」（与 browser_login 同属用户直达例外）。
  if (
    escalation.status === "pending" &&
    (escalation.ownershipPaths?.length ?? 0) > 0
  ) {
    return (
      <PendingOwnershipEscalation
        escalation={escalation}
        role={role}
        conversationId={conversationId}
        interactive={interactive}
      />
    );
  }
  // D1: CEO arbitration pending — visible but not user-answerable.
  if (escalation.status === "pending" && escalation.awaiting === "ceo") {
    return <AwaitingCeoEscalation escalation={escalation} role={role} />;
  }
  if (!interactive) {
    return <DormantEscalation escalation={escalation} role={role} />;
  }
  return (
    <PendingEscalation
      escalation={escalation}
      role={role}
      conversationId={conversationId}
    />
  );
}

function useEscalationSubmit(
  conversationId: string | null,
  escalationId: string | null,
) {
  const [submitting, setSubmitting] = useState<
    EscalationUserDecision["kind"] | null
  >(null);
  // 回执说这条已经结了：按钮就此关掉（再点也只会 404），但不转圈——那一帧
  // `escalation_resolved` 可能早就过去了，等不来。收口文案走提示，卡面结果等线材帧。
  const [settled, setSettled] = useState(false);
  const busy = submitting !== null || settled;

  const send = (decision: EscalationUserDecision) => {
    if (busy || !conversationId || !escalationId) return;
    setSubmitting(decision.kind);
    decideEscalation(conversationId, escalationId, decision)
      .then((result) => {
        if (result === "already_settled") setSettled(true);
        if (result !== "ok") {
          notifySubmitInteractionResult(result);
          setSubmitting(null);
        }
        // ok: SSE escalation_resolved settles the card
      })
      .catch((err) => {
        notifyError(err, "提交失败");
        setSubmitting(null);
      });
  };

  return { submitting, busy, send };
}

/** 浏览器登录等待 escalate：不 auto-resume；用户接管登录后点「已登录，继续」resolve。 */
function PendingBrowserLoginEscalation({
  escalation,
  role,
  conversationId,
}: {
  escalation: RunEscalation;
  role: string;
  conversationId: string | null;
}) {
  const { submitting, busy, send } = useEscalationSubmit(
    conversationId,
    escalation.id,
  );
  const submitKind =
    submitting === "answer"
      ? ("logged_in" as const)
      : submitting === "use_assumption"
        ? ("use_assumption" as const)
        : null;
  return (
    <BrowserLoginDecisionCard
      roleLabel={role}
      question={escalation.question}
      assumption={escalation.assumption || undefined}
      conversationId={conversationId}
      revealKey={escalation.id ?? "browser-login"}
      timeoutSeconds={escalation.timeoutSeconds}
      busy={busy}
      submitting={submitKind}
      kindTag={escalationKindTag(escalation) || undefined}
      onLoggedIn={() => send({ kind: "answer", answer: "已登录，继续" })}
      onUseAssumption={() => send({ kind: "use_assumption" })}
    />
  );
}

function PendingOwnershipEscalation({
  escalation,
  role,
  conversationId,
  interactive,
}: {
  escalation: RunEscalation;
  role: string;
  conversationId: string | null;
  interactive: boolean;
}) {
  const { submitting, busy, send } = useEscalationSubmit(
    conversationId,
    escalation.id,
  );
  const paths = escalation.ownershipPaths ?? [];
  if (!interactive) {
    return <DormantEscalation escalation={escalation} role={role} />;
  }
  return (
    <DecisionCard tone="primary" animate>
      <div className="flex items-start gap-2">
        <DecisionCardIcon tone="primary">
          <HelpCircle size={16} />
        </DecisionCardIcon>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1">
            <p className="min-w-0 flex-1 text-xs font-medium text-primary">
              {role} · 文件写权冲突
              {escalationKindTag(escalation)
                ? ` · ${escalationKindTag(escalation)}`
                : ""}
            </p>
            <ManualHelpLink to={MANUAL_HELP.control} />
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            选择是否把下列路径的写权交给升级方
          </p>
          <ul className="mt-1.5 list-inside list-disc text-xs text-foreground">
            {paths.map((p) => (
              <li key={p} className="font-mono">
                {p}
              </li>
            ))}
          </ul>
          {escalation.lockOwnerRunId ? (
            <p className="mt-1.5 text-xs text-muted-foreground">
              当前写主：`{escalation.lockOwnerRunId}`
            </p>
          ) : null}
          <p className="mt-0.5 whitespace-pre-wrap text-sm text-foreground">
            {escalation.question}
          </p>
          {escalation.assumption ? (
            <p className="mt-2 rounded-lg bg-card/60 px-2.5 py-1.5 text-xs text-muted-foreground">
              不移交写权时将按此继续（目标路径修订会落空）：
              {escalation.assumption}
            </p>
          ) : (
            <p className="mt-2 rounded-lg bg-card/60 px-2.5 py-1.5 text-xs text-muted-foreground">
              「保持原主 / 按假设继续」均不移交写权，升级方无法改上述路径。
            </p>
          )}
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-1.5 pl-6">
        <Button
          variant="primary"
          disabled={busy}
          onClick={() => send({ kind: "transfer_ownership" })}
          icon={
            submitting === "transfer_ownership" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Check size={13} />
            )
          }
        >
          移交写权
        </Button>
        <Button
          variant="neutral"
          disabled={busy}
          onClick={() => send({ kind: "keep_ownership" })}
          icon={
            submitting === "keep_ownership" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <ArrowRight size={13} />
            )
          }
        >
          保持原主
        </Button>
        <Button
          variant="neutral"
          disabled={busy}
          onClick={() => send({ kind: "use_assumption" })}
          icon={
            submitting === "use_assumption" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <ArrowRight size={13} />
            )
          }
        >
          按假设继续（不移交）
        </Button>
      </div>
    </DecisionCard>
  );
}

function PendingEscalation({
  escalation,
  role,
  conversationId,
}: {
  escalation: RunEscalation;
  role: string;
  conversationId: string | null;
}) {
  // 结构化升级: reuse the ask_user 问答内核 (choice/text + 答复模型 α composition). A worker
  // fork is always a 待你拍板 (no 起步计划 / 风格), so the content carries only the structured
  // `questions`; the free note doubles as the answer box for a plain free-text escalate.
  const content: AskUserContent = {
    question: escalation.question,
    assumptions: [],
    questions: escalation.questions,
  };
  const ans = useAskAnswer(content);
  const tone = interactiveCheckpointTone.primary;
  const { submitting, busy, send } = useEscalationSubmit(
    conversationId,
    escalation.id,
  );
  const hasStructured = escalation.questions.length > 0;
  // composeAnswer flattens picks + note into one readable string (a worker reads it like the
  // CEO does); for a free-text escalate it is just the note. 提交 needs a non-empty answer.
  const composed = ans.compose("decision");
  const canSubmit = composed.trim().length > 0;

  return (
    <DecisionCard tone="primary" animate>
      <div className="flex items-start gap-2">
        <DecisionCardIcon tone="primary">
          <HelpCircle size={16} />
        </DecisionCardIcon>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1">
            <p className="min-w-0 flex-1 text-xs font-medium text-primary">
              {role} · 请你拍板
              {escalationKindTag(escalation)
                ? ` · ${escalationKindTag(escalation)}`
                : ""}
            </p>
            <ManualHelpLink to={MANUAL_HELP.control} />
          </div>
          <p className="mt-0.5 whitespace-pre-wrap text-sm text-foreground">
            {escalation.question}
          </p>
          <p className="mt-2 rounded-lg bg-card/60 px-2.5 py-1.5 text-xs text-muted-foreground">
            {escalationWaitNote({
              assumption: escalation.assumption,
              timeoutSeconds: escalation.timeoutSeconds,
            })}
          </p>
          <div className="mt-2 space-y-3">
            {hasStructured && (
              <AskQuestionFields
                content={content}
                answer={ans}
                tone={tone}
                disabled={busy}
                disclosureKey={escalation.id}
              />
            )}
            <AskNoteField
              answer={ans}
              tone={tone}
              disabled={busy}
              placeholder={
                hasStructured
                  ? "可选 · 补充说明"
                  : "输入你的决定（留空则点「按假设继续」）"
              }
            />
          </div>
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-1.5 pl-6">
        <Button
          variant="primary"
          disabled={busy || !canSubmit}
          onClick={() => send({ kind: "answer", answer: composed })}
          icon={
            submitting === "answer" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Check size={13} />
            )
          }
        >
          提交
        </Button>
        <Button
          variant="neutral"
          disabled={busy}
          onClick={() => send({ kind: "use_assumption" })}
          icon={
            submitting === "use_assumption" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <ArrowRight size={13} />
            )
          }
        >
          按假设继续
        </Button>
      </div>
    </DecisionCard>
  );
}

function AwaitingCeoEscalation({
  escalation,
  role,
}: {
  escalation: RunEscalation;
  role: string;
}) {
  return (
    <DecisionCard tone="neutral">
      <div className="flex items-start gap-2">
        <DecisionCardIcon tone="neutral">
          <Loader2 size={16} className="animate-spin" />
        </DecisionCardIcon>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-muted-foreground">
            {role} · 等待主管仲裁
            {escalationKindTag(escalation)
              ? ` · ${escalationKindTag(escalation)}`
              : ""}
          </p>
          <p className="mt-0.5 whitespace-pre-wrap text-sm text-foreground">
            {escalation.question}
          </p>
          <p className="mt-2 rounded-lg bg-card/60 px-2.5 py-1.5 text-xs text-muted-foreground">
            {escalationWaitNote({
              assumption: escalation.assumption,
              timeoutSeconds: escalation.timeoutSeconds,
              awaiting: "ceo",
            })}
          </p>
        </div>
      </div>
    </DecisionCard>
  );
}

function DormantEscalation({
  escalation,
  role,
}: {
  escalation: RunEscalation;
  role: string;
}) {
  return (
    <DecisionCard tone="neutral">
      <div className="flex items-start gap-2">
        <DecisionCardIcon tone="neutral">
          <HelpCircle size={16} />
        </DecisionCardIcon>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-muted-foreground">
            {role} 曾请你拍板（本回合已结束）
          </p>
          <p className="mt-0.5 whitespace-pre-wrap text-sm text-foreground">
            {escalation.question}
          </p>
          <p className="mt-1.5 text-xs text-muted-foreground">
            暂定假设：{escalation.assumption}
          </p>
        </div>
      </div>
    </DecisionCard>
  );
}

/** 卡住早停 source（validation_thrash / ceiling_backstop）——非「边干边上报」。 */
function isEarlyStopSource(source: string | undefined): boolean {
  return source === "validation_thrash" || source === "ceiling_backstop";
}

/** 非阻塞 raised（run_escalation）:
 * - 真·边干边上报：被动 notice，标题「边干边上报（无需你拍板）」；有 assumption 才渲染「暂定假设」。
 * - 卡住早停（source=validation_thrash|ceiling_backstop）：标题「卡住早停（交付可能不完整）」；
 *   正文 question；不写边干边上报 / 已按假设继续 / 无需你拍板。
 * 默认收起为一行（对齐 TeamPreview / ResolvedDecisionRecord），点开再看全文。 */
function RaisedEscalation({
  escalation,
  role,
}: {
  escalation: RunEscalation;
  role: string;
}) {
  const kind = escalationKindTag(escalation);
  const earlyStop = isEarlyStopSource(escalation.source);
  const summary = earlyStop
    ? `${role} · 卡住早停（交付可能不完整）${kind ? ` · ${kind}` : ""}`
    : `${role} · 边干边上报（无需你拍板）${kind ? ` · ${kind}` : ""}`;
  return (
    <ResolvedDecisionRecord
      layout="neutralCollapsible"
      disclosureKey={escalationDisclosureKey(escalation, role, "raised")}
      icon={earlyStop ? AlertTriangle : Megaphone}
      summary={summary}
    >
      <p className="mt-0.5 whitespace-pre-wrap text-sm text-foreground">
        {escalation.question}
      </p>
      {!earlyStop && escalation.assumption ? (
        <p className="mt-1.5 text-xs text-muted-foreground">
          暂定假设：{escalation.assumption}
        </p>
      ) : null}
    </ResolvedDecisionRecord>
  );
}

function ResolvedEscalation({
  escalation,
  role,
}: {
  escalation: RunEscalation;
  role: string;
}) {
  const byCeo = escalation.arbitrated_by === "ceo";
  const viaUser = byCeo && escalation.via_user === true;
  const assumed = escalation.status === "assumed";
  const timedOut = escalation.status === "timed_out";
  const isFallback = assumed || timedOut;
  const ownershipConflict = (escalation.ownershipPaths?.length ?? 0) > 0;
  let headline: string;
  if (assumed) {
    headline = ownershipConflict
      ? byCeo
        ? "主管选按假设继续（未移交写权）"
        : "你选了按假设继续（未移交写权）"
      : byCeo
        ? "主管选按假设继续"
        : "你选了按假设继续";
  } else if (timedOut) {
    headline = ownershipConflict
      ? byCeo
        ? "主管未裁 · 超时按假设（未移交写权）"
        : "超时未答 · 已按假设继续（未移交写权）"
      : byCeo
        ? "主管未裁 · 超时按假设继续"
        : "超时未答 · 已按假设继续";
  } else if (byCeo) {
    headline = viaUser ? "CEO 已仲裁（经用户）" : "CEO 已仲裁";
  } else {
    headline = "已答复";
  }
  return (
    <ResolvedDecisionRecord
      layout="neutralCollapsible"
      disclosureKey={escalationDisclosureKey(escalation, role, "resolved")}
      icon={isFallback ? Clock : Check}
      summary={`${role} · ${headline}`}
    >
      <p className="mt-0.5 whitespace-pre-wrap text-sm text-foreground">
        {escalation.question}
      </p>
      {isFallback ? (
        <p className="mt-1.5 text-xs text-muted-foreground">
          {timedOut ? "超时回落假设：" : "按假设继续："}
          {escalation.assumption}
        </p>
      ) : (
        escalation.answer && (
          <p className="mt-1.5 whitespace-pre-wrap rounded-lg bg-muted/50 px-2.5 py-1.5 text-xs text-foreground">
            {escalation.answer}
          </p>
        )
      )}
    </ResolvedDecisionRecord>
  );
}

/** 列表序对齐 faceBudget：待拍板 > 已结案 > 边干边上报（raised 置底降噪）。 */
export function escalationListRank(status: RunEscalation["status"]): number {
  if (status === "pending") return 0;
  if (status === "raised") return 2;
  return 1; // resolved / assumed / timed_out
}

/** ≥2 条 raised，或同时有待拍板时，默认收起边干边上报。 */
export function shouldCollapseRaised(
  raisedCount: number,
  pendingCount: number,
): boolean {
  return raisedCount >= 2 || (raisedCount >= 1 && pendingCount >= 1);
}

export function EscalationCards({
  messageId,
  conversationId,
  interactive,
}: {
  messageId: string;
  conversationId: string | null;
  interactive: boolean;
}) {
  const execution = useMessageExecution(messageId);
  const [raisedOpen, setRaisedOpen] = useState(false);
  if (!execution) return null;

  const roleById = new Map(execution.agents.map((a) => [a.id, a.role]));
  const items = execution.runs.flatMap((run) =>
    run.escalations.map((e, i) => ({
      esc: e,
      role: roleById.get(run.agentId) ?? run.agentId,
      key: e.id ?? `${run.id}-${i}`,
    })),
  );
  if (items.length === 0) return null;

  const ordered = [...items].sort(
    (a, b) =>
      escalationListRank(a.esc.status) - escalationListRank(b.esc.status),
  );
  const pending = ordered.filter((i) => i.esc.status === "pending");
  const settled = ordered.filter(
    (i) =>
      i.esc.status === "resolved" ||
      i.esc.status === "assumed" ||
      i.esc.status === "timed_out",
  );
  const raised = ordered.filter((i) => i.esc.status === "raised");
  const collapseRaised = shouldCollapseRaised(raised.length, pending.length);
  const showRaisedCards = !collapseRaised || raisedOpen;

  return (
    <div className="mt-2 space-y-2">
      {pending.length > 0 && (
        <p className="text-xs font-medium text-primary">
          团队有 {pending.length} 项待你拍板
        </p>
      )}
      {pending.map((i) => (
        <EscalationCard
          key={i.key}
          escalation={i.esc}
          role={i.role}
          conversationId={conversationId}
          interactive={interactive}
        />
      ))}
      {settled.map((i) => (
        <EscalationCard
          key={i.key}
          escalation={i.esc}
          role={i.role}
          conversationId={conversationId}
          interactive={interactive}
        />
      ))}
      {raised.length > 0 && collapseRaised && (
        <button
          type="button"
          className="flex w-full items-center gap-1.5 rounded-lg bg-card/60 px-2.5 py-2 text-left text-xs text-muted-foreground hover:bg-card"
          onClick={() => setRaisedOpen((v) => !v)}
          aria-expanded={showRaisedCards}
        >
          {showRaisedCards ? (
            <ChevronDown size={14} className="shrink-0" />
          ) : (
            <ChevronRight size={14} className="shrink-0" />
          )}
          <Megaphone size={14} className="shrink-0" />
          <span>
            {raised.length} 条边干边上报（无需你拍板）
            {showRaisedCards ? " · 收起" : " · 展开"}
          </span>
        </button>
      )}
      {showRaisedCards &&
        raised.map((i) => (
          <EscalationCard
            key={i.key}
            escalation={i.esc}
            role={i.role}
            conversationId={conversationId}
            interactive={interactive}
          />
        ))}
    </div>
  );
}
