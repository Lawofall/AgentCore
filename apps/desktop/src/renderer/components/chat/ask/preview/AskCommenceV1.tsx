/**
 * V1 Compact Decision — Linear-style confirm.
 * Dense header, options dominate, CTA sticky at bottom.
 */
import { Rocket } from "lucide-react";
import { useState } from "react";
import type { AskUserContent } from "../AskUserFields";
import {
  ChoiceQuestion,
  CommenceFooter,
  CommenceNote,
  PlanDetails,
  PreviewShell,
  useCommencePreviewAnswer,
} from "./AskCommenceShared";

export function AskCommenceV1({ content }: { content: AskUserContent }) {
  const answer = useCommencePreviewAnswer(content);
  const [busy, setBusy] = useState(false);
  const noop = () => {
    setBusy(true);
    window.setTimeout(() => setBusy(false), 600);
  };

  return (
    <PreviewShell data-variant="ask-commence-v1">
      <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-3 pt-3">
        <div className="flex items-center gap-1.5">
          <Rocket size={14} className="shrink-0 text-muted-foreground" />
          <p className="text-xs font-medium text-muted-foreground">
            开工提案 · 确认即开做
          </p>
        </div>
        <p className="line-clamp-2 text-sm font-medium text-foreground">
          {content.question}
        </p>

        <PlanDetails
          assumptions={content.assumptions}
          disclosureKey="preview:ask-commence-v1"
        />

        <div className="space-y-3 border-t border-border pt-2.5">
          {content.questions.map((q, i) => (
            <ChoiceQuestion
              key={q.id}
              question={q}
              index={i + 1}
              numbered={content.questions.length > 1}
              answer={answer.answers[q.id] ?? []}
              otherOn={answer.otherOn[q.id] ?? false}
              otherText={answer.otherText[q.id] ?? ""}
              disabled={busy}
              onToggle={(opt) => answer.toggleChoice(q, opt)}
              onToggleOther={() => answer.toggleOther(q)}
              onSetOther={(v) => answer.setOtherValue(q, v)}
            />
          ))}
        </div>

        <CommenceNote answer={answer} disabled={busy} compact />
      </div>

      <CommenceFooter
        answer={answer}
        busy={busy}
        onContinue={noop}
        onStop={noop}
        sticky
      />
    </PreviewShell>
  );
}
