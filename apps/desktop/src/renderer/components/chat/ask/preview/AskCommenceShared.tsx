/**
 * Preview chrome for 开工提案 layout A/B.
 * Shared option/brief pieces live in production {@link ../AskCommenceParts};
 * this file keeps preview-only shell + footer + answer alias.
 */
import { Button } from "@/components/ui";
import { Loader2, Rocket } from "lucide-react";
import type { ReactNode } from "react";
import {
  COMMENCE_TONE,
  ChoiceQuestion,
  CommenceNote,
  OptionButton,
  splitBriefContext,
} from "../AskCommenceParts";
import type { AskUserContent } from "../AskUserFields";
import { useAskAnswer } from "../AskUserFields";

export { ChoiceQuestion, CommenceNote, OptionButton, splitBriefContext };
export { COMMENCE_TONE as PREVIEW_TONE };

export type PreviewAnswer = ReturnType<typeof useAskAnswer>;

export function useCommencePreviewAnswer(content: AskUserContent) {
  return useAskAnswer(content);
}

/** Sticky / fixed footer: primary CTA + quiet skip (wire stop) + preset hint. */
export function CommenceFooter({
  answer,
  busy,
  onContinue,
  onStop,
  className = "",
  sticky = false,
}: {
  answer: PreviewAnswer;
  busy: boolean;
  onContinue: () => void;
  onStop: () => void;
  className?: string;
  sticky?: boolean;
}) {
  return (
    <div
      className={`${sticky ? "shrink-0 border-t border-border bg-card/95 backdrop-blur-sm" : ""} space-y-1.5 px-3 py-3 ${className}`}
    >
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button size="md" variant="outline" disabled={busy} onClick={onStop}>
          取消
        </Button>
        <Button
          size="md"
          variant="primary"
          className={COMMENCE_TONE.cta}
          disabled={busy}
          onClick={onContinue}
          icon={
            busy ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Rocket size={14} />
            )
          }
        >
          就这样开做
        </Button>
      </div>
      <span className="block text-xs text-muted-foreground">
        {answer.presetCount > 0
          ? `已预填 ${answer.presetCount} 项，直接开做或按需调整`
          : "也可直接在下方对话框回复"}
      </span>
    </div>
  );
}

export function PreviewShell({
  children,
  className = "",
  "data-variant": dataVariant,
}: {
  children: ReactNode;
  className?: string;
  "data-variant": string;
}) {
  return (
    <div
      data-ask-commence-variant={dataVariant}
      className={`flex max-h-[min(70vh,36rem)] flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm ${className}`}
    >
      {children}
    </div>
  );
}
