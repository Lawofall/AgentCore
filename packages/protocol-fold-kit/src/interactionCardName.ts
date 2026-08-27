/**
 * 交互 kind → 用户面卡名（桌面 / 手机同一份）。
 *
 * 展示文案，不是 wire 语义。kind 穷尽靠 `Record<UserInteractionKind, _>`：
 * 后端加 kind 后 `pnpm gen:types`，本表漏写即编不过。未知 kind 走独立兜底，
 * 不从原型或邻键继承别人的名字。
 */
import type { UserInteractionKind } from "@agentcore/contract-types";

export const INTERACTION_CARD_NAME = {
  approval: "工具审批",
  escalation: "拍板请求",
  ask_user: "提问确认",
  plan_review: "计划复核",
  stage_card: "推进卡",
} as const satisfies Record<UserInteractionKind, string>;

/** 不在表里的 kind（含尚未 codegen 的）——不是某张卡的名字。 */
export const INTERACTION_CARD_NAME_UNKNOWN = "确认";

export function interactionCardName(kind: string): string {
  if (Object.hasOwn(INTERACTION_CARD_NAME, kind)) {
    return INTERACTION_CARD_NAME[kind as UserInteractionKind];
  }
  return INTERACTION_CARD_NAME_UNKNOWN;
}
