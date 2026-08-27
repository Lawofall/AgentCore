/**
 * UI bindings for {@link INTERACTION_REGISTRY} — card components + cold-resume
 * renderers. Kept separate from the data registry to avoid React cycles in
 * store / fold modules.
 */

import { CheckpointCard } from "@/components/chat/CheckpointCard";
import { EscalationCard } from "@/components/chat/EscalationCard";
import {
  ApprovalTrace,
  StageCardTrace,
} from "@/components/chat/HotDecisionTrace";
import { type RunEscalation, useMessageExecution } from "@/stores/execution";
import { useInteractionStore } from "@/stores/interactions";
import type { ReactNode } from "react";
import type { TimelineProcessKind } from "./registry";
import {
  type TimelineCardBags,
  classifyTimelineInteractionCard,
  timelineIntentionalEmpty,
  timelineMissingCard,
} from "./timelineCardSlot";

export type { TimelineCardBags } from "./timelineCardSlot";

type TimelineNodeId = {
  checkpoint_id?: string;
  escalation_id?: string;
  approval_id?: string;
  stage_card_id?: string;
};

export type TimelineRenderCtx = {
  messageId: string;
  conversationId: string | null;
  interactive: boolean;
};

/**
 * Render the inline decision card / 痕迹 for a timeline process marker.
 *
 * Two empty paths (do not collapse them back to a bare `null`):
 * - {@link timelineIntentionalEmpty}: 设计上不画（`plan_review` 只在 ResumePrompt）。
 * - {@link timelineMissingCard}: 有标记但袋子/store 里没有实体；dev 占位，prod 仍空白。
 */
export function renderTimelineInteractionCard(
  processKind: TimelineProcessKind,
  node: TimelineNodeId,
  bags: TimelineCardBags,
  ctx?: TimelineRenderCtx,
): ReactNode {
  const slot = classifyTimelineInteractionCard(processKind, node, bags, ctx);
  if (slot.kind === "intentionalEmpty") return timelineIntentionalEmpty();
  if (slot.kind === "missing") return timelineMissingCard(slot);

  switch (processKind) {
    case "checkpoint": {
      const cp = bags.checkpoints.find((c) => c.id === node.checkpoint_id);
      if (!cp) {
        return timelineMissingCard({
          kind: "missing",
          processKind,
          id: node.checkpoint_id,
        });
      }
      return <CheckpointCard key={cp.id} checkpoint={cp} />;
    }
    case "escalation": {
      if (!ctx?.messageId || !node.escalation_id) {
        return timelineMissingCard({
          kind: "missing",
          processKind,
          id: node.escalation_id,
        });
      }
      return (
        <EscalationTimelineSlot
          key={node.escalation_id}
          escalationId={node.escalation_id}
          messageId={ctx.messageId}
          conversationId={ctx.conversationId}
          interactive={ctx.interactive}
        />
      );
    }
    case "approval": {
      if (!node.approval_id) {
        return timelineMissingCard({
          kind: "missing",
          processKind,
          id: node.approval_id,
        });
      }
      return (
        <ApprovalTrace
          key={node.approval_id}
          approvalId={node.approval_id}
          messageId={ctx?.messageId ?? ""}
        />
      );
    }
    case "stage_card": {
      if (!node.stage_card_id) {
        return timelineMissingCard({
          kind: "missing",
          processKind,
          id: node.stage_card_id,
        });
      }
      return (
        <StageCardTrace
          key={node.stage_card_id}
          stageCardId={node.stage_card_id}
        />
      );
    }
    default: {
      processKind satisfies "plan_review";
      return timelineIntentionalEmpty();
    }
  }
}

/** One escalation at its own timeline marker (统一时间线二期 D2). */
function EscalationTimelineSlot({
  escalationId,
  messageId,
  conversationId,
  interactive,
}: {
  escalationId: string;
  messageId: string;
  conversationId: string | null;
  interactive: boolean;
}) {
  const execution = useMessageExecution(messageId);
  const orphaned = useInteractionStore((s) => {
    const e = s.byId.get(escalationId);
    return e?.kind === "escalation" && e.status === "orphaned";
  });
  // Orphaned: silent dismiss (no tombstone card) — 有意为空.
  if (orphaned) return timelineIntentionalEmpty();

  if (!execution) {
    return timelineMissingCard({
      kind: "missing",
      processKind: "escalation",
      id: escalationId,
    });
  }

  let found: { esc: RunEscalation; role: string } | null = null;
  for (const run of execution.runs) {
    const esc = run.escalations.find((e) => e.id === escalationId);
    if (esc) {
      const role =
        execution.agents.find((a) => a.id === run.agentId)?.role ?? run.agentId;
      found = { esc, role };
      break;
    }
  }
  if (!found) {
    return timelineMissingCard({
      kind: "missing",
      processKind: "escalation",
      id: escalationId,
    });
  }

  return (
    <EscalationCard
      escalation={found.esc}
      role={found.role}
      conversationId={conversationId}
      interactive={interactive}
    />
  );
}
