/**
 * V4 Executive Summary — Cursor / ChatGPT confirm-bar upgrade.
 * One-line verdict + param pills on top; compact option list below.
 */
import { Rocket } from "lucide-react";
import { useState } from "react";
import type { AskUserContent } from "../AskUserFields";
import {
  ChoiceQuestion,
  CommenceFooter,
  CommenceNote,
  PlanChips,
  PreviewShell,
  useCommencePreviewAnswer,
} from "./AskCommenceShared";

export function AskCommenceV4({ content }: { content: AskUserContent }) {
  const answer = useCommencePreviewAnswer(content);
  const [busy, setBusy] = useState(false);
  const noop = () => {
    setBusy(true);
    window.setTimeout(() => setBusy(false), 600);
  };

  const summaryPicks = content.questions
    .map((q) => {
      const picked = answer.answers[q.id] ?? [];
      return picked[0] ?? q.default ?? "未选";
    })
    .join(" · ");

  return (
    <PreviewShell data-variant="ask-commence-v4">
      {/* Executive strip — the distinctive IA */}
      <div className="shrink-0 space-y-2 border-b border-border bg-muted/20 px-3 py-3">
        <div className="flex items-start gap-2">
          <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Rocket size={14} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-muted-foreground">
              开工提案 · 确认即开做
            </p>
            <p className="mt-0.5 text-sm font-medium text-foreground">
              将按「{summaryPicks || "默认方案"}」开做
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {content.question}
            </p>
          </div>
        </div>
        <PlanChips assumptions={content.assumptions} />
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-2.5">
        <p className="text-xs font-medium text-muted-foreground">关键决策</p>
        {content.questions.map((q, i) => (
          <ChoiceQuestion
            key={q.id}
            question={q}
            index={i + 1}
            numbered={content.questions.length > 1}
            answer={answer.answers[q.id] ?? []}
            disabled={busy}
            onToggle={(opt) => answer.toggleChoice(q, opt)}
            askAnswer={answer}
          />
        ))}
        {content.questions.length === 0 && (
          <CommenceNote answer={answer} disabled={busy} compact />
        )}
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
