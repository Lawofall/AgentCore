/**
 * V3 Wizard Step — one focal question at a time.
 * Progress chrome restrained; plan params as quiet chips; large option cards.
 */
import { Button } from "@/components/ui";
import { ChevronLeft, ChevronRight } from "lucide-react";
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

export function AskCommenceV3({ content }: { content: AskUserContent }) {
  const answer = useCommencePreviewAnswer(content);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState(0);
  const total = content.questions.length;
  const q = content.questions[step];
  const isLast = step === total - 1;
  const noop = () => {
    setBusy(true);
    window.setTimeout(() => setBusy(false), 600);
  };

  return (
    <PreviewShell
      data-variant="ask-commence-v3"
      className="max-h-[min(78vh,40rem)]"
    >
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 pt-4 pb-2">
          {/* Quiet chrome: title + thin progress */}
          <div className="space-y-2">
            <div className="flex items-baseline justify-between gap-3">
              <p className="text-xs font-medium text-muted-foreground">
                开工提案 · 确认即开做
              </p>
              {total > 1 && (
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground/70">
                  {step + 1}/{total}
                </span>
              )}
            </div>
            <p className="line-clamp-1 text-xs text-muted-foreground/70">
              {content.question}
            </p>
            {total > 1 && (
              // Progress dots are navigated via the child buttons; the bar itself is not a tab stop.
              // biome-ignore lint/a11y/useFocusableInteractive: child buttons own keyboard focus
              <div
                className="flex items-center gap-1"
                role="progressbar"
                aria-valuenow={step + 1}
                aria-valuemin={1}
                aria-valuemax={total}
              >
                {content.questions.map((item, i) => (
                  <button
                    key={item.id}
                    type="button"
                    aria-label={`第 ${i + 1} 题`}
                    aria-current={i === step ? "step" : undefined}
                    onClick={() => setStep(i)}
                    className={`h-1 flex-1 rounded-full transition-colors ${
                      i === step
                        ? "bg-foreground/50"
                        : i < step
                          ? "bg-foreground/20"
                          : "bg-muted"
                    }`}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Focal question */}
          {q && (
            <div className="pt-1">
              <ChoiceQuestion
                question={q}
                index={step + 1}
                numbered={false}
                answer={answer.answers[q.id] ?? []}
                disabled={busy}
                onToggle={(opt) => answer.toggleChoice(q, opt)}
                optionLayout="card"
                emphasizePrompt
                optionSize="lg"
              />
            </div>
          )}

          {total > 1 && (
            <div className="flex items-center justify-between gap-2">
              <Button
                variant="ghost"
                size="md"
                disabled={busy || step === 0}
                onClick={() => setStep((s) => Math.max(0, s - 1))}
                icon={<ChevronLeft size={14} />}
                className="text-muted-foreground"
              >
                上一题
              </Button>
              <Button
                variant="ghost"
                size="md"
                disabled={busy || isLast}
                onClick={() => setStep((s) => Math.min(total - 1, s + 1))}
                icon={<ChevronRight size={14} />}
                className="text-muted-foreground"
              >
                下一题
              </Button>
            </div>
          )}

          {/* Secondary: plan + style/note only after decisions */}
          <div className="space-y-2 border-t border-border/50 pt-3">
            <p className="text-xs text-muted-foreground">起步计划</p>
            <PlanChips assumptions={content.assumptions} quiet />
            {isLast && <CommenceNote answer={answer} disabled={busy} compact />}
          </div>
        </div>

        <CommenceFooter
          answer={answer}
          busy={busy}
          onContinue={noop}
          onStop={noop}
          sticky
        />
      </div>
    </PreviewShell>
  );
}
