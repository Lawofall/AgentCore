import { Markdown } from "@/components/chat/Markdown";
import {
  hasTaggedSections,
  parsePromptDocument,
} from "@/lib/parsePromptDocument";
import { cn } from "@/lib/utils";
import { useMemo } from "react";

/** Structured prompt / skill body: tagged sections rendered as Markdown.
 * Shared by 工具箱能力图鉴 and consult / consult_skill result cards. */
export function PromptDocument({
  text,
  className,
  maxHeightClass = "max-h-[32rem]",
  compact = true,
}: {
  text: string;
  className?: string;
  /** Tailwind max-height utility for the scroll container. */
  maxHeightClass?: string;
  /** Catalog / skill cards stay compact (`xs`). Reading surfaces pass `false` (`sm`). */
  compact?: boolean;
}) {
  const sections = useMemo(() => parsePromptDocument(text), [text]);
  const structured = hasTaggedSections(sections);

  if (!text.trim()) return null;

  const bodyClass = compact
    ? "markdown-body markdown-body--compact text-foreground/90"
    : "markdown-body";
  const titleClass = compact
    ? "font-medium text-foreground text-xs"
    : "font-medium text-foreground text-sm";

  return (
    <div className={cn("space-y-2", className)}>
      {structured ? (
        <div
          className={cn(
            "space-y-3 overflow-auto rounded-lg bg-muted/50 px-3 py-2",
            maxHeightClass,
          )}
        >
          {sections.map((section, i) => (
            <section
              key={`${section.tag ?? "preamble"}-${i}`}
              className="space-y-1"
            >
              {section.title ? (
                <h3 className={titleClass}>{section.title}</h3>
              ) : null}
              <div className={bodyClass}>
                <Markdown content={section.body} muted={compact} />
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div
          className={cn(
            "overflow-auto rounded-lg bg-muted/50 px-3 py-2",
            bodyClass,
            maxHeightClass,
          )}
        >
          <Markdown content={text} muted={compact} />
        </div>
      )}
    </div>
  );
}
