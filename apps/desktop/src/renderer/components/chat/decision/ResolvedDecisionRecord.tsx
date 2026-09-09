import { Button } from "@/components/ui";
import { resolvedCheckpointTone } from "@/components/ui/tone-presets";
import { usePersistentDisclosure } from "@/stores/disclosure";
import { ChevronDown, ChevronRight, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { ResolvedToneKey } from "./meta";

/**
 * Settled ask / escalation as a process row.
 * Short copy hugs (Thought); long copy truncates at the row. Tool rows keep their own tail.
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

const ROW =
  "h-auto w-auto max-w-full min-w-0 justify-start gap-2 overflow-hidden px-0 py-0 text-sm font-normal text-muted-foreground hover:bg-transparent hover:text-foreground";

function ProcessRowIcon({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <span className="flex h-5 shrink-0 items-center justify-center text-muted-foreground">
      <Icon size={14} />
    </span>
  );
}

function ProcessRowChevron({ open }: { open: boolean }) {
  return open ? (
    <ChevronDown size={14} className="shrink-0" />
  ) : (
    <ChevronRight size={14} className="shrink-0" />
  );
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
  const title = label.trim();
  const summary =
    !open && collapsedSummary != null && collapsedSummary !== ""
      ? collapsedSummary
      : "";

  return (
    <div
      className={`min-w-0 max-w-full${tone.wrap ? ` ${tone.wrap}` : ""}`}
      data-ask-intent={askIntent}
      data-ask-status="resolved"
    >
      <Button
        variant="ghost"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={title || summary ? undefined : "拍板记录"}
        className={ROW}
      >
        <ProcessRowIcon icon={DecisionIcon} />
        <span className="flex h-5 min-w-0 items-center gap-1.5 overflow-hidden text-left">
          {title !== "" ? (
            <span className={`shrink-0 text-sm ${tone.label}`}>{title}</span>
          ) : null}
          {summary !== "" ? (
            <span className="min-w-0 truncate text-sm">{summary}</span>
          ) : null}
        </span>
        <ProcessRowChevron open={open} />
      </Button>
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
    <div className="min-w-0 max-w-full">
      <Button
        variant="ghost"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={ROW}
      >
        <ProcessRowIcon icon={Icon} />
        <span className="h-5 min-w-0 truncate text-left text-sm leading-5">
          {summary}
        </span>
        <ProcessRowChevron open={open} />
      </Button>
      {open && children}
    </div>
  );
}
