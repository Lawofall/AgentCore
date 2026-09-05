import { BrowserLoginDecisionCard } from "@/components/chat/BrowserLoginDecisionCard";
import { AskUserCard } from "@/components/chat/CheckpointCard";
import { DecisionCard, DecisionCardIcon } from "@/components/ui";
import { notifyError } from "@/lib/toast";
import {
  notifySubmitInteractionResult,
  submitInteraction,
  submitInteractionFeedback,
} from "@/services/interactionSubmit";
import type { PlanReviewUserDecision } from "@/services/planReview";
import { useInteractionStore } from "@/stores/interactions";
import type { PendingResume } from "@/stores/pausedTurns";
import { MessageCircleQuestion } from "lucide-react";
import { useState } from "react";
import { ResumeDeferredNotice } from "./ResumeDeferredNotice";

function AskUserBrowserLoginResumeCard({ turn }: { turn: PendingResume }) {
  const [submitting, setSubmitting] = useState<"logged_in" | "stop" | null>(
    null,
  );
  const entryStatus = useInteractionStore(
    (s) => s.byId.get(turn.checkpointId)?.status,
  );
  const deferredBusyReason = useInteractionStore(
    (s) => s.byId.get(turn.checkpointId)?.resumeDeferred?.busyReason ?? null,
  );
  const busy =
    submitting !== null ||
    entryStatus === "submitting" ||
    deferredBusyReason !== null;

  if (deferredBusyReason) {
    return (
      <DecisionCard tone="neutral" animate className="mx-0 p-3">
        <div className="flex items-start gap-2">
          <DecisionCardIcon tone="neutral">
            <MessageCircleQuestion size={16} />
          </DecisionCardIcon>
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-sm font-semibold text-foreground">已记下</p>
            <ResumeDeferredNotice busyReason={deferredBusyReason} />
          </div>
        </div>
      </DecisionCard>
    );
  }

  const send = async (decision: "continue" | "stop") => {
    if (busy) return;
    setSubmitting(decision === "continue" ? "logged_in" : "stop");
    try {
      const result = await submitInteraction({
        id: turn.checkpointId,
        kind: "ask_user",
        conversationId: turn.conversationId,
        cold: {
          messageId: turn.messageId,
          decision,
          note: decision === "continue" ? "已登录，继续" : "",
          selected: [],
        },
      });
      if (result !== "ok") {
        notifySubmitInteractionResult(result);
        setSubmitting(null);
      }
    } catch (err) {
      notifyError(err, "提交失败");
      setSubmitting(null);
    }
  };

  return (
    <BrowserLoginDecisionCard
      roleLabel="主 Agent"
      question={turn.question || "请在右坞浏览器完成登录"}
      conversationId={turn.conversationId}
      revealKey={turn.checkpointId}
      busy={busy}
      submitting={submitting}
      onLoggedIn={() => void send("continue")}
      onStop={() => void send("stop")}
    />
  );
}

/** Cold-path ask_user resume card — reuses hot AskUserCard / browser-login shell. */
export function AskUserResumeCard({ turn }: { turn: PendingResume }) {
  const deferredBusyReason = useInteractionStore(
    (s) => s.byId.get(turn.checkpointId)?.resumeDeferred?.busyReason ?? null,
  );

  if (deferredBusyReason) {
    return (
      <DecisionCard tone="neutral" animate className="mx-0 p-3">
        <div className="flex items-start gap-2">
          <DecisionCardIcon tone="neutral">
            <MessageCircleQuestion size={16} />
          </DecisionCardIcon>
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-sm font-semibold text-foreground">已记下</p>
            <ResumeDeferredNotice busyReason={deferredBusyReason} />
          </div>
        </div>
      </DecisionCard>
    );
  }

  if (turn.browserLogin) {
    return <AskUserBrowserLoginResumeCard turn={turn} />;
  }
  return (
    <AskUserCard
      content={turn}
      intent={turn.intent}
      disclosureKey={turn.checkpointId}
      conversationId={turn.conversationId}
      onSubmit={async (decision, note, selected = []) => {
        const result = await submitInteraction({
          id: turn.checkpointId,
          kind: "ask_user",
          conversationId: turn.conversationId,
          cold: {
            messageId: turn.messageId,
            decision: decision as PlanReviewUserDecision,
            note,
            selected,
          },
        });
        if (result !== "ok") {
          throw new Error(submitInteractionFeedback(result));
        }
      }}
    />
  );
}
