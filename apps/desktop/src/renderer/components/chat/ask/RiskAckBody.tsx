/**
 * risk_ack — 风险勾选清单：行式多选（与 kickoff 同壳）。
 * label「[高]/[中]/[低]」前缀解析为右侧灰字严重度；recommended → 灰字「建议处理」（无彩色徽章）。
 */
import { MANUAL_HELP, ManualHelpLink } from "@/components/ManualHelpLink";
import { ASK_INTENT_META } from "@/components/chat/decision";
import type { CheckpointUserDecision } from "@/services/checkpoint";
import { AskCardFooter, AskCardShell } from "./AskCardShell";
import { CommenceNote } from "./AskCommenceParts";
import { AskRowGroup } from "./AskOptionRow";
import type { AskUserContent, useAskAnswer } from "./AskUserFields";
import { RISK_SEVERITY_META, parseRiskLabel } from "./parseRiskLabel";

const META = ASK_INTENT_META.risk_ack;

export function RiskAckBody({
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
      variant="risk_ack"
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
        />
      }
    >
      <div className="space-y-3">
        {q && (
          <div>
            {q.prompt && (
              <p className="px-2 text-xs font-medium leading-snug text-foreground">
                {q.prompt}
                <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                  可多选
                </span>
              </p>
            )}
            <AskRowGroup
              className={q.prompt ? "mt-1" : undefined}
              multiple
              rows={q.options.map((opt) => {
                const { severity, text } = parseRiskLabel(opt.label);
                const hints: string[] = [];
                if (severity) hints.push(RISK_SEVERITY_META[severity].tag);
                if (opt.recommended && q.default !== opt.label) {
                  hints.push("建议处理");
                }
                return {
                  key: opt.label,
                  label: text,
                  detail: opt.detail,
                  hint: hints.length ? hints.join(" · ") : undefined,
                  selected: picked.includes(opt.label),
                  disabled: busy,
                  onSelect: () => answer.toggleChoice(q, opt.label),
                };
              })}
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
