/**
 * proposal_pick — 方案挑选：行式单选（与 kickoff 同壳）。
 * 方案墙卡片感有意弱化；彩色推荐徽章已删，仅当 recommended ≠ default 时右侧灰字「推荐」。
 */
import { MANUAL_HELP, ManualHelpLink } from "@/components/ManualHelpLink";
import { ASK_INTENT_META } from "@/components/chat/decision";
import type { CheckpointUserDecision } from "@/services/checkpoint";
import { AskCardFooter, AskCardShell } from "./AskCardShell";
import { CommenceNote } from "./AskCommenceParts";
import { AskRowGroup } from "./AskOptionRow";
import type { AskUserContent, useAskAnswer } from "./AskUserFields";

const META = ASK_INTENT_META.proposal_pick;

export function ProposalPickBody({
  content,
  answer,
  busy,
  submitting,
  caption,
  onContinue,
  onStop,
}: {
  content: AskUserContent;
  answer: ReturnType<typeof useAskAnswer>;
  busy: boolean;
  submitting: CheckpointUserDecision | null;
  caption?: string;
  onContinue: () => void;
  onStop: () => void;
}) {
  const q = content.questions[0];
  const picked = q ? (answer.answers[q.id] ?? []) : [];

  return (
    <AskCardShell
      variant="proposal_pick"
      icon={META.icon}
      caption={caption ?? META.activeCaption}
      title={content.question}
      extra={<ManualHelpLink to={MANUAL_HELP.checkpoint} />}
      footer={
        <AskCardFooter
          cta={META.cta}
          ctaIcon={META.ctaIcon}
          busy={busy}
          submitting={submitting}
          onContinue={onContinue}
          onStop={onStop}
          ctaDisabled={picked.length === 0}
        />
      }
    >
      <div className="space-y-3">
        {q && (
          <div>
            {q.prompt && (
              <p className="px-2 text-xs font-medium leading-snug text-foreground">
                {q.prompt}
              </p>
            )}
            <AskRowGroup
              className={q.prompt ? "mt-1" : undefined}
              rows={q.options.map((opt) => ({
                key: opt.label,
                label: opt.label,
                detail: opt.detail,
                hint:
                  opt.recommended && q.default !== opt.label
                    ? "推荐"
                    : undefined,
                selected: picked.includes(opt.label),
                disabled: busy,
                onSelect: () => answer.toggleChoice(q, opt.label),
              }))}
            />
          </div>
        )}

        <div className="px-2">
          <CommenceNote answer={answer} disabled={busy} compact />
        </div>
      </div>
    </AskCardShell>
  );
}
