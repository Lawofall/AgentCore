/**
 * 非阻塞挂问（hanging question）用户面文案 —— 底栏标题 / CTA / 默认假设提示。
 * 两端必须逐字一致：这不是「暂停拍板」，不能复用检查点的「提交」。
 *
 * `formatHangingDefault` 只是把 assumptions 拼成一句提示，不含 fold / 事件归并。
 * 会话级 pending 收集、`question_resolved` 跨列表结算等仍留在各端。
 */

/** 底栏脸：「有事等你」但团队继续跑（不是冻住）。 */
export const HANGING_QUESTION_CAPTION = "有事等你，团队照跑";

/** CTA —— 不是检查点的「提交」。 */
export const HANGING_QUESTION_CTA = "答复";

export const HANGING_QUESTION_DEFAULT_HINT = "没回之前按这个继续";

/**
 * CEO 回合已结束、团队脱图在跑时的诚实提示。
 * 新回合答复接不上那张还在跑的图（已知接缝；本模块不修）。
 */
export const HANGING_QUESTION_DETACHED_HINT =
  "答了会作为新消息发出；后台还在跑的那张图这轮接不上";

/**
 * 与两端 `AskAssumption` 同形（kit 不引契约包）。
 * 成文只读 label/value；`id` 是线上假设行的稳定键，类型必须带着，不能为避依赖而丢掉。
 */
export interface HangingAssumptionCopy {
  id: string;
  label: string;
  value: string;
}

/** 「没回之前按这个继续：label：value；…」；没有可用假设时返回 null。 */
export function formatHangingDefault(
  assumptions: readonly HangingAssumptionCopy[] | undefined,
): string | null {
  if (!assumptions?.length) return null;
  const parts = assumptions
    .map((a) => {
      const label = a.label?.trim() ?? "";
      const value = a.value?.trim() ?? "";
      if (label && value) return `${label}：${value}`;
      return value || label;
    })
    .filter(Boolean);
  if (parts.length === 0) return null;
  return `${HANGING_QUESTION_DEFAULT_HINT}：${parts.join("；")}`;
}
