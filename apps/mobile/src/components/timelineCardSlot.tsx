/**
 * 时间线交互卡槽位：把以前共用的 `null` 拆成两条路径。
 *
 * 手机自写（不共享桌面 fold / 业务核）。产品分工与桌面不同：
 * checkpoint / plan_review 时间线永远不画（操作面在 Sheet / ResumeCard）。
 *
 * - {@link timelineIntentionalEmpty}：设计上不画。开发/生产都是空白。
 * - {@link timelineMissingCard}：时间线有标记但袋子/痕迹里没有实体。开发态画占位，生产仍空白。
 */
import { Fragment, type ReactNode } from "react";

export type MobileTimelineProcessKind =
  | "checkpoint"
  | "plan_review"
  | "team_preview"
  | "escalation"
  | "approval"
  | "stage_card"
  | "user_interjection";

export type TimelineSlotIds = {
  checkpoint_id?: string;
  escalation_id?: string;
  approval_id?: string;
  stage_card_id?: string;
  interjection_id?: string;
};

export type TimelineSlotLookup = {
  escalationSlots?: ReadonlyMap<string, unknown>;
  hotTraces?: ReadonlyMap<string, { resolved: boolean }>;
  stageCardTraces?: ReadonlyMap<string, { outcome: string }>;
  teamPreviewTraces?: ReadonlyMap<string, { status: string }>;
  userInterjections?: ReadonlyArray<{ interjectionId: string }>;
};

export type TimelineCardSlot =
  | { kind: "card" }
  | { kind: "intentionalEmpty" }
  | { kind: "missing"; processKind: MobileTimelineProcessKind; id?: string };

export const TIMELINE_MISSING_CARD_TEST_ID = "timeline-missing-card";

function noteUnhandledTimelineKind(_x: never): TimelineCardSlot {
  return { kind: "intentionalEmpty" };
}

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
      className="timeline-missing-card"
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
 * `card` → `undefined`（调用方继续画实体）；其余 → 空槽节点（有意为空是 null，
 * missing 在开发态是占位）。给 map 子节点带上 `nodeKey`。
 */
export function timelineEmptyNode(
  nodeKey: string,
  slot: TimelineCardSlot,
): ReactNode | undefined {
  if (slot.kind === "card") return undefined;
  const ph =
    slot.kind === "missing"
      ? timelineMissingCard(slot)
      : timelineIntentionalEmpty();
  return ph ? <Fragment key={nodeKey}>{ph}</Fragment> : null;
}

/**
 * 手机时间线标记：先分类再决定画不画。
 *
 * `checkpoint` / `plan_review` 永远有意为空（Sheet / ResumeCard）。
 * 走袋子的 kind 未命中就是 missing；热痕迹再拆 pending（有意为空）vs 未命中（missing）。
 */
export function classifyTimelineInteractionCard(
  processKind: MobileTimelineProcessKind,
  node: TimelineSlotIds,
  lookup: TimelineSlotLookup,
): TimelineCardSlot {
  switch (processKind) {
    case "checkpoint":
    case "plan_review":
      return { kind: "intentionalEmpty" };
    case "team_preview": {
      const id = node.checkpoint_id;
      if (!id) return { kind: "missing", processKind, id };
      const t = lookup.teamPreviewTraces?.get(id);
      if (!t) return { kind: "missing", processKind, id };
      if (t.status === "pending") return { kind: "intentionalEmpty" };
      return { kind: "card" };
    }
    case "escalation": {
      const id = node.escalation_id;
      if (id && lookup.escalationSlots?.has(id)) {
        return { kind: "card" };
      }
      return { kind: "missing", processKind, id };
    }
    case "approval": {
      const id = node.approval_id;
      if (!id) return { kind: "missing", processKind, id };
      const t = lookup.hotTraces?.get(id);
      if (!t) return { kind: "missing", processKind, id };
      if (!t.resolved) return { kind: "intentionalEmpty" };
      return { kind: "card" };
    }
    case "stage_card": {
      const id = node.stage_card_id;
      if (!id) return { kind: "missing", processKind, id };
      const t = lookup.stageCardTraces?.get(id);
      if (!t) return { kind: "missing", processKind, id };
      if (t.outcome === "pending") return { kind: "intentionalEmpty" };
      return { kind: "card" };
    }
    case "user_interjection": {
      const id = node.interjection_id;
      if (
        id &&
        lookup.userInterjections?.some((u) => u.interjectionId === id)
      ) {
        return { kind: "card" };
      }
      return { kind: "missing", processKind, id };
    }
    default:
      return noteUnhandledTimelineKind(processKind);
  }
}
