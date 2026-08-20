/**
 * 消息出口（复制 / 分享）里两端已经对齐的段落标题与过程行 chrome。
 *
 * 拼稿函数、工具英文名表、rework 时态、剪贴板 DOM 仍留在各端：
 * 工具表覆盖面不同，rework 桌面跟直播态、手机只出完成态。
 */

export type MessageCopyMode = "deliverable" | "with_process";

export const MESSAGE_EXPORT_REASONING_HEADING = "【思考】";
export const MESSAGE_EXPORT_PROCESS_HEADING = "【过程】";
export const MESSAGE_EXPORT_DELIVERABLE_HEADING = "【交付】";

export const MESSAGE_EXPORT_STEP_CHROME = {
  team: "· （团队协作）",
  checkpoint: "· （向你确认）",
  plan_review: "· （计划复核）",
  team_preview: "· （团队预览）",
} as const;

export const MESSAGE_EXPORT_TOOL_STATUS_SUFFIX = {
  error: "（失败）",
  running: "（进行中）",
} as const;
