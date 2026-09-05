import {
  Button,
  DecisionCard,
  DecisionCardIcon,
  Textarea,
} from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import type { PlanReviewUserDecision } from "@/services/planReview";
import type { PendingResume } from "@/stores/pausedTurns";
import { GitBranch, Loader2, Pencil } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ConclusionHero } from "./ConclusionHero";
import { PlanReviewContextBand } from "./PlanReviewContextBand";
import { ResumeDeferredNotice } from "./ResumeDeferredNotice";
import { useColdSubmit } from "./useColdSubmit";

/** Cold-path plan_review resume card (拍板中心). */
export function PlanReviewResumeCard({ turn }: { turn: PendingResume }) {
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const { submitting, busy, deferredBusyReason, send } = useColdSubmit(turn);
  const settlementLocked = deferredBusyReason !== null;

  const spinnerOr = (
    decision: PlanReviewUserDecision,
    icon?: React.ReactNode,
  ) =>
    submitting === decision || (settlementLocked && decision === "continue") ? (
      <Loader2 size={13} className="animate-spin" />
    ) : (
      icon
    );

  // 拍板中心：时间线挂起态不画标记，等谁 / 等什么 / 产出引用都在这张卡。
  const reviewedRoles = turn.steps.map((s) => s.role).filter(Boolean);
  const rolesLabel =
    reviewedRoles.length > 0 ? `「${reviewedRoles.join("、")}」` : "这一步";
  const hasDownstream = turn.pending.length > 0;
  const title = hasDownstream
    ? `${rolesLabel}已完成，是否放行下游？`
    : `${rolesLabel}已完成`;
  const disclosureKey = turn.checkpointId;
  const gateHint = turn.ceoReview?.source === "llm";

  useEffect(() => {
    if (noteOpen) noteRef.current?.focus();
  }, [noteOpen]);

  const continueBtn = (
    <Button
      variant="primary"
      icon={spinnerOr("continue")}
      disabled={busy}
      onClick={() => send("continue", [], note.trim())}
      aria-label={gateHint ? "继续。继续后，把关要点将发给下游" : undefined}
    >
      {settlementLocked ? "已记下" : "继续"}
    </Button>
  );

  return (
    <DecisionCard
      tone="primary"
      animate
      className="mx-0 flex max-h-[min(60vh,36rem)] flex-col overflow-hidden p-0"
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
          <div className="flex items-start gap-2">
            <DecisionCardIcon tone="primary">
              <GitBranch size={16} />
            </DecisionCardIcon>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-primary">计划复核</p>
              <p className="mt-0.5 text-sm font-semibold text-foreground">
                {title}
              </p>
              {turn.ceoReview?.conclusion && (
                <ConclusionHero text={turn.ceoReview.conclusion} />
              )}
              <PlanReviewContextBand
                turn={turn}
                disclosureKey={disclosureKey}
              />
            </div>
          </div>
        </div>

        <div className="shrink-0 space-y-2 border-t border-border bg-card/95 px-3 py-3 backdrop-blur-sm">
          {settlementLocked && deferredBusyReason ? (
            <ResumeDeferredNotice busyReason={deferredBusyReason} />
          ) : null}
          {noteOpen && !settlementLocked && (
            <Textarea
              ref={noteRef}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={busy}
              rows={2}
              placeholder="可选备注；调整时必填"
              className="w-full border-border bg-card/70 focus:border-primary/60"
              data-testid="plan-review-note"
            />
          )}
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            {!settlementLocked ? (
              <Button
                variant="outline"
                icon={spinnerOr("stop")}
                disabled={busy}
                onClick={() => send("stop", [], note.trim())}
              >
                取消
              </Button>
            ) : null}
            {!settlementLocked ? (
              <Button
                variant="neutral"
                icon={spinnerOr("adjust", <Pencil size={13} />)}
                disabled={busy}
                onClick={() => {
                  if (!note.trim()) {
                    if (noteOpen) {
                      noteRef.current?.focus();
                    } else {
                      setNoteOpen(true);
                    }
                    return;
                  }
                  send("adjust", [], note.trim());
                }}
              >
                调整
              </Button>
            ) : null}
            {gateHint && !settlementLocked ? (
              <SimpleTooltip label="继续后，把关要点将发给下游">
                <span
                  className="inline-flex"
                  data-testid="plan-review-gate-notes-hint"
                >
                  {continueBtn}
                </span>
              </SimpleTooltip>
            ) : (
              continueBtn
            )}
          </div>
        </div>
      </div>
    </DecisionCard>
  );
}
