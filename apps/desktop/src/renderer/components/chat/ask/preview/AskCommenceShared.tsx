/**
 * Preview chrome for 开工提案 layout A/B.
 * Shared option/brief pieces live in production {@link ../AskCommenceParts};
 * this file keeps preview-only shell + footer + answer alias.
 */
import { Button } from "@/components/ui";
import { usePersistentDisclosure } from "@/stores/disclosure";
import type { AskAssumption } from "@/types/events";
import { ChevronRight, Loader2, OctagonX, Rocket } from "lucide-react";
import type { ReactNode } from "react";
import {
  COMMENCE_TONE,
  ChoiceQuestion,
  CommenceNote,
  OptionButton,
  PlanChips,
  splitBriefContext,
} from "../AskCommenceParts";
import type { AskUserContent } from "../AskUserFields";
import { useAskAnswer } from "../AskUserFields";

export {
  ChoiceQuestion,
  CommenceNote,
  OptionButton,
  PlanChips,
  splitBriefContext,
};
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
        <Button
          size="md"
          variant="ghost"
          disabled={busy}
          onClick={onStop}
          className="text-muted-foreground hover:text-foreground"
          icon={<OctagonX size={14} />}
        >
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

/** Collapsible 起步计划 — same semantics as production, slightly denser. */
export function PlanDetails({
  assumptions,
  defaultOpen = false,
  disclosureKey,
}: {
  assumptions: AskAssumption[];
  defaultOpen?: boolean;
  /** Preview/stable key；缺省退化为会话内存态。 */
  disclosureKey?: string | null;
}) {
  const [open, setOpen] = usePersistentDisclosure(
    disclosureKey ? `${disclosureKey}:assumptions` : null,
    defaultOpen,
  );
  if (assumptions.length === 0) return null;
  return (
    <div className="rounded-lg bg-muted/20">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 text-left"
      >
        <ChevronRight
          size={13}
          className={`shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
        />
        <span className="shrink-0 text-xs font-medium text-muted-foreground">
          起步计划
        </span>
        {!open && (
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground/70">
            {assumptions.map((a) => a.label).join(" · ")}
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-0.5 px-2.5 pb-2 pl-6">
          {assumptions.map((a) => (
            <div key={a.id} className="flex gap-1.5 text-xs">
              <span className="w-14 shrink-0 text-muted-foreground">
                {a.label}
              </span>
              <span className="min-w-0 flex-1 whitespace-pre-wrap text-foreground">
                {a.value}
              </span>
            </div>
          ))}
        </div>
      )}
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
