import { InteractionLane } from "@/components/chat/InteractionLane";
import { ProcessLane } from "@/components/chat/ProcessLane";
import { SourceCards } from "@/components/chat/SourceCards";
import { TeamLane } from "@/components/chat/TeamLane";
import {
  type ChatTurnInput,
  resolveChatTurn,
  slottedResolvedAskIds,
} from "@/components/chat/chatTurn";
import { CollapsibleBody } from "@/components/conversation-replay/shared";
import { cn } from "@/lib/utils";

/**
 * Admin AI-chat final-state shell. Eats a replay assistant row's combination:
 * content + `runs_payload` + nullable `projected` — not a client fold.
 *
 * `/replay/:id` mounts this via {@link chatTurnFromReplay}.
 */
export function ChatView({
  content,
  runsPayload,
  projected,
  reasoningContent,
  className,
  selectedRunId,
  onSelectRun,
}: ChatTurnInput & {
  className?: string;
  selectedRunId?: string | null;
  onSelectRun?: (runId: string) => void;
}) {
  const turn = resolveChatTurn({
    content,
    runsPayload,
    projected,
    reasoningContent,
  });
  const hasReasoningStep = turn.process.some((s) => s.kind === "reasoning");
  const fallbackReasoning =
    !hasReasoningStep && turn.reasoning.trim() ? turn.reasoning : "";
  const body = turn.content.trim();
  const processHasContent = turn.process.some((s) => s.kind === "content");
  const teamLane =
    turn.runs.length > 0 ? (
      <TeamLane
        runs={turn.runs}
        progress={turn.progress}
        selectedRunId={selectedRunId}
        onSelectRun={onSelectRun}
      />
    ) : null;
  const bodyBlock = body ? (
    <CollapsibleBody content={turn.content} />
  ) : null;
  const leftoverInteractions = turn.interactions.filter(
    (item) => !slottedResolvedAskIds(turn.process, turn.interactions).has(item.id),
  );

  return (
    <div
      aria-label="对话终态"
      className={cn("flex flex-col gap-4", className)}
    >
      {turn.turnWarning && (
        <p className="rounded-lg bg-muted px-3 py-2 text-sm text-foreground">
          {turn.turnWarning}
        </p>
      )}

      {turn.error && (
        <p className="rounded-lg bg-destructive-tint px-3 py-2 text-destructive text-sm">
          {turn.error.message || turn.error.code}
        </p>
      )}

      <article className="min-w-0 w-full space-y-3">
        <ProcessLane
          steps={turn.process}
          fallbackReasoning={fallbackReasoning}
          hideContentSteps={Boolean(body)}
          interactions={turn.interactions}
          fallbackContent={bodyBlock}
          team={teamLane}
        />
        {!body &&
        !processHasContent &&
        turn.runs.length === 0 &&
        turn.process.length === 0 &&
        leftoverInteractions.length === 0 ? (
          <p className="text-muted-foreground text-sm italic">（无正文）</p>
        ) : null}
        <SourceCards citations={turn.citations} />
        {turn.debate && (
          <p className="text-sm text-muted-foreground">
            辩论
            {turn.debate.form ? ` · ${turn.debate.form}` : ""}
            {turn.debate.motion ? ` · ${turn.debate.motion}` : ""}
          </p>
        )}
        {turn.deliveryStatus?.state && (
          <p className="text-xs text-muted-foreground">
            交付 {turn.deliveryStatus.state}
          </p>
        )}
        <InteractionLane interactions={leftoverInteractions} />
      </article>
    </div>
  );
}
