/**
 * 时间线交互卡槽位：把以前共用的 `null` 拆成两条路径。
 *
 * - {@link timelineIntentionalEmpty}：设计上不画（plan_review、pending 热痕迹、挂起即收口）。开发/生产都是空白。
 * - {@link timelineMissingCard}：时间线有标记但袋子/store 里没有卡。开发态画占位，生产仍空白。
 */
import { assertNever } from "@/lib/assertNever";
import type {
  CheckpointDisplay,
  PlanReviewDisplay,
} from "@/stores/conversation";
import type { ReactNode } from "react";
import type { TimelineProcessKind } from "./registry";

export type TimelineCardBags = {
  checkpoints: CheckpointDisplay[];
  planReviews: PlanReviewDisplay[];
};

export type TimelineSlotNodeId = {
  checkpoint_id?: string;
  escalation_id?: string;
  approval_id?: string;
  stage_card_id?: string;
};

export type TimelineSlotCtx = {
  messageId?: string;
};

export type TimelineCardSlot =
  | { kind: "card" }
  | { kind: "intentionalEmpty" }
  | { kind: "missing"; processKind: TimelineProcessKind; id?: string };

export const TIMELINE_MISSING_CARD_TEST_ID = "timeline-missing-card";

/** 有意为空：设计上不画。与 {@link timelineMissingCard} 相对。 */
export function timelineIntentionalEmpty(): null {
  return null;
}

/** 生产像素不变；测试可分别打两条分支。 */
export function missingTimelineCardNode(
  info: Extract<TimelineCardSlot, { kind: "missing" }>,
  isDev: boolean,
): ReactNode {
  if (!isDev) return null;
  const id = info.id ?? "";
  return (
    <div
      data-testid={TIMELINE_MISSING_CARD_TEST_ID}
      data-process-kind={info.processKind}
      data-card-id={id}
      className="rounded-lg border border-dashed border-border px-2 py-1 text-xs text-muted-foreground"
    >
      {`[dev] 时间线有 ${info.processKind} 标记但卡片实体缺失${id ? `（${id}）` : ""}`}
    </div>
  );
}

/** 查不到卡：开发态可见占位，生产仍为 null。 */
export function timelineMissingCard(
  info: Extract<TimelineCardSlot, { kind: "missing" }>,
): ReactNode {
  return missingTimelineCardNode(info, import.meta.env.DEV);
}

/**
 * 时间线标记先分类再决定画不画。
 *
 * 走袋子的 kind（`checkpoint`）id 不在 {@link TimelineCardBags} 里就是 missing。
 * `plan_review` / `team_preview` 永远有意为空（开工卡已退役；plan_review 操作面只在
 * ResumePrompt）。热痕迹（`approval` / `stage_card` / `escalation`）标记带 id 即
 * `card`，挂上的组件再拆 pending（有意为空）vs store 未命中（missing）。
 */
export function classifyTimelineInteractionCard(
  processKind: TimelineProcessKind,
  node: TimelineSlotNodeId,
  bags: TimelineCardBags,
  ctx?: TimelineSlotCtx,
): TimelineCardSlot {
  switch (processKind) {
    case "plan_review":
    case "team_preview":
      return { kind: "intentionalEmpty" };
    case "checkpoint": {
      const id = node.checkpoint_id;
      if (id && bags.checkpoints.some((c) => c.id === id)) {
        return { kind: "card" };
      }
      return { kind: "missing", processKind, id };
    }
    case "escalation": {
      if (!ctx?.messageId || !node.escalation_id) {
        return { kind: "missing", processKind, id: node.escalation_id };
      }
      return { kind: "card" };
    }
    case "approval": {
      if (!node.approval_id) {
        return { kind: "missing", processKind, id: node.approval_id };
      }
      return { kind: "card" };
    }
    case "stage_card": {
      if (!node.stage_card_id) {
        return { kind: "missing", processKind, id: node.stage_card_id };
      }
      return { kind: "card" };
    }
    default:
      return assertNever(processKind);
  }
}
