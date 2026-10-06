import { Button } from "@/components/ui";
import { resolvedCheckpointTone } from "@/components/ui/tone-presets";
import { usePersistentDisclosure } from "@/stores/disclosure";
import { ChevronRight, type LucideIcon } from "lucide-react";
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
        /** Single-string scan line. Ignored when `summaryStem` / `summaryAnswer` are set. */
        collapsedSummary?: string;
        /** Question identity. Stays on the row while the body is open. */
        summaryStem?: string;
        /** Reply. Stays at the end of the row so a long stem truncates first. */
        summaryAnswer?: string;
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
  return (
    <ChevronRight
      size={14}
      className={`shrink-0 transition-transform duration-200 ease-out motion-reduce:transition-none${open ? " rotate-90" : ""}`}
    />
  );
}

function ToneStubRecord({
  disclosureKey,
  tone: toneKey,
  icon: DecisionIcon,
  label,
  collapsedSummary,
  summaryStem,
  summaryAnswer,
  askIntent,
  children,
}: {
  layout: "toneStub";
  disclosureKey: string | null;
  tone: ResolvedToneKey;
  icon: LucideIcon;
  label: string;
  collapsedSummary?: string;
  summaryStem?: string;
  summaryAnswer?: string;
  askIntent?: string;
  children: ReactNode;
}) {
  const tone = resolvedCheckpointTone[toneKey];
  const [open, setOpen] = usePersistentDisclosure(disclosureKey, false);
  const title = label.trim();
  const stem = (summaryStem ?? "").trim();
  const answer = (summaryAnswer ?? "").trim();
  const summary =
    stem === "" && answer === "" && collapsedSummary != null
      ? collapsedSummary.trim()
      : "";
  const expandable = children != null && children !== false;
  const unnamed =
    title === "" && stem === "" && answer === "" && summary === "";
  const answerClass =
    stem !== "" || title !== ""
      ? "ml-1.5 min-w-0 max-w-64 shrink-0 truncate text-sm"
      : "min-w-0 truncate text-sm";

  const row = (
    <>
      <ProcessRowIcon icon={DecisionIcon} />
      <span className="flex h-5 min-w-0 items-center overflow-hidden text-left">
        {title !== "" ? (
          <span className={`shrink-0 text-sm ${tone.label}`}>{title}</span>
        ) : null}
        {stem !== "" ? (
          <span
            data-ask-stem=""
            className={`min-w-0 truncate text-sm${title !== "" ? " ml-1.5" : ""}`}
          >
            {stem}
          </span>
        ) : null}
        {answer !== "" ? (
          <span data-ask-answer="" className={answerClass}>
            {stem !== "" || title !== "" ? `· ${answer}` : answer}
          </span>
        ) : null}
        {summary !== "" ? (
          <span className="min-w-0 truncate text-sm">{summary}</span>
        ) : null}
      </span>
      {expandable ? <ProcessRowChevron open={open} /> : null}
    </>
  );

  return (
    <div
      className={`min-w-0 max-w-full${tone.wrap ? ` ${tone.wrap}` : ""}`}
      data-ask-intent={askIntent}
      data-ask-status="resolved"
    >
      {expandable ? (
        <Button
          variant="ghost"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={unnamed ? "拍板记录" : undefined}
          className={ROW}
        >
          {row}
        </Button>
      ) : (
        <div
          className={`inline-flex items-center ${ROW}`}
          aria-label={unnamed ? "拍板记录" : undefined}
        >
          {row}
        </div>
      )}
      {expandable ? (
        <div
          className="grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none"
          style={{ gridTemplateRows: open ? "1fr" : "0fr" }}
          data-ask-open={open ? "true" : "false"}
        >
          <div
            className="min-h-0 overflow-hidden"
            aria-hidden={open ? undefined : true}
          >
            {children}
          </div>
        </div>
      ) : null}
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
