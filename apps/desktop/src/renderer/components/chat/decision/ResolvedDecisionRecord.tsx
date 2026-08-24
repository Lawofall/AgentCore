import { resolvedCheckpointTone } from "@/components/ui/tone-presets";
import { usePersistentDisclosure } from "@/stores/disclosure";
import { ChevronDown, ChevronRight, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { ResolvedToneKey } from "./meta";

/**
 * Shared settled-record shell for ask_user.
 *
 * Timeline metadata light card (ghost) — not the white DecisionCard box used by
 * live collaboration / pending decisions. Callers pick toneStub vs
 * neutralCollapsible; meta labels / icons come from {@link ./meta}.
 */
export function ResolvedDecisionRecord(
  props:
    | {
        layout: "toneStub";
        disclosureKey: string | null;
        tone: ResolvedToneKey;
        icon: LucideIcon;
        label: string;
        collapsedSummary?: string;
        askIntent?: string;
        children: ReactNode;
      }
    | {
        layout: "neutralCollapsible";
        disclosureKey: string;
        icon: LucideIcon;
        summary: string;
        children: ReactNode;
      },
) {
  if (props.layout === "toneStub") {
    return <ToneStubRecord {...props} />;
  }
  return <NeutralCollapsibleRecord {...props} />;
}

function ToneStubRecord({
  disclosureKey,
  tone: toneKey,
  icon: DecisionIcon,
  label,
  collapsedSummary,
  askIntent,
  children,
}: {
  layout: "toneStub";
  disclosureKey: string | null;
  tone: ResolvedToneKey;
  icon: LucideIcon;
  label: string;
  collapsedSummary?: string;
  askIntent?: string;
  children: ReactNode;
}) {
  const tone = resolvedCheckpointTone[toneKey];
  const [open, setOpen] = usePersistentDisclosure(disclosureKey, false);

  return (
    <div
      className={`mt-2 animate-task-card-enter motion-reduce:animate-none${tone.wrap ? ` ${tone.wrap}` : ""}`}
      data-ask-intent={askIntent}
      data-ask-status="resolved"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={
          label.trim() || collapsedSummary?.trim() ? undefined : "拍板记录"
        }
        className="flex w-full items-center gap-2 py-1.5 text-left"
      >
        <span
          className={`flex size-5 shrink-0 items-center justify-center rounded-full ${tone.badge}`}
        >
          <DecisionIcon size={14} />
        </span>
        {label.trim() !== "" ? (
          <span className={`shrink-0 text-xs font-medium ${tone.label}`}>
            {label}
          </span>
        ) : null}
        {!open && collapsedSummary != null && collapsedSummary !== "" && (
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {collapsedSummary}
          </span>
        )}
        <ChevronRight
          size={14}
          className={`ml-auto shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
        />
      </button>
      {open && children}
    </div>
  );
}

function NeutralCollapsibleRecord({
  disclosureKey,
  icon: Icon,
  summary,
  children,
}: {
  layout: "neutralCollapsible";
  disclosureKey: string;
  icon: LucideIcon;
  summary: string;
  children: ReactNode;
}) {
  const [open, setOpen] = usePersistentDisclosure(disclosureKey, false);

  return (
    <div className="mt-2 animate-task-card-enter">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0 text-muted-foreground">
          <Icon size={14} />
        </span>
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="flex w-full items-start gap-1.5 text-left"
          >
            <span className="min-w-0 flex-1 text-xs font-medium text-muted-foreground">
              {summary}
            </span>
            {open ? (
              <ChevronDown
                size={14}
                className="mt-0.5 shrink-0 text-muted-foreground"
              />
            ) : (
              <ChevronRight
                size={14}
                className="mt-0.5 shrink-0 text-muted-foreground"
              />
            )}
          </button>
          {open && children}
        </div>
      </div>
    </div>
  );
}
