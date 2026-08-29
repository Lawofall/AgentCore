import type { MentionSectionId } from "./composerAttachments";

/** 按实际频率：文件/文件夹前移，团队下沉。附件仍由调用方置顶。 */
export const MENTION_CATEGORY_ORDER: MentionSectionId[] = [
  "file",
  "folder",
  "conversation",
  "setting",
  "team",
];

export const MENTION_LIST_TRUNCATED_HINT =
  "仅显示部分结果，输入关键词可搜索全部";

export const MENTION_CATEGORY_LABEL: Record<MentionSectionId, string> = {
  team: "团队",
  conversation: "对话",
  folder: "文件夹",
  file: "文件",
  setting: "设定",
};

/** 一级目录行：顶行「附件」+ 四类。附件不是 MentionSectionId，禁止 drill。 */
export type MentionCategoryId = MentionSectionId | "attach";

export interface MentionCategoryRow {
  id: MentionCategoryId;
  label: string;
  count: number;
  disabled: boolean;
  hint?: string;
  loading?: boolean;
}

export const MENTION_ATTACH_CATEGORY: MentionCategoryRow = {
  id: "attach",
  label: "附件",
  count: 0,
  disabled: false,
  hint: "从本机添加",
};

export function isMentionSectionId(
  id: MentionCategoryId,
): id is MentionSectionId {
  return id !== "attach";
}

/** 空查询且未钻入 / 无类型前缀 → 一级目录。 */
export function showMentionCategoryLevel(opts: {
  sectionFilter: MentionSectionId | null;
  activeCategory: MentionSectionId | null;
  filterText: string;
}): boolean {
  return (
    opts.sectionFilter === null &&
    opts.activeCategory === null &&
    !opts.filterText.trim()
  );
}

export function buildMentionCategoryRows(input: {
  counts: Record<MentionSectionId, number>;
  loadingFiles?: boolean;
}): MentionCategoryRow[] {
  return [
    MENTION_ATTACH_CATEGORY,
    ...MENTION_CATEGORY_ORDER.flatMap((id): MentionCategoryRow[] => {
      const count = input.counts[id];
      if ((id === "team" || id === "setting") && count === 0) return [];
      return [
        {
          id,
          label: MENTION_CATEGORY_LABEL[id],
          count,
          disabled: false,
          loading:
            Boolean(input.loadingFiles) &&
            (id === "folder" || id === "file") &&
            count === 0,
        },
      ];
    }),
  ];
}

/** 手打 @ 优先团队；团队隐藏时落到文件，避免高亮空附件行。 */
export function categoryHighlightIndex(
  categories: readonly { id: string }[],
  want: "team" | "attach" | "file",
): number {
  const exact = categories.findIndex((c) => c.id === want);
  if (exact >= 0) return exact;
  const file = categories.findIndex((c) => c.id === "file");
  if (file >= 0) return file;
  const next = categories.findIndex((c) => c.id !== "attach");
  return next >= 0 ? next : 0;
}

export type MentionMenuKeyAction =
  | { type: "move"; index: number }
  | { type: "drill" }
  | { type: "attach" }
  | { type: "back" }
  | { type: "select" }
  | { type: "close" }
  | { type: "consume" }
  | { type: "ignore" };

export function mentionMenuKeyAction(
  key: string,
  state: {
    showCategoryLevel: boolean;
    categoryCount: number;
    activeIndex: number;
    categoryDisabled: boolean;
    /** 一级「附件」：Enter/Tab 选文件，ArrowRight 不 drill。 */
    categoryAttach: boolean;
    itemCount: number;
    canKeyBack: boolean;
  },
): MentionMenuKeyAction {
  const lastCat = Math.max(state.categoryCount - 1, 0);
  const lastItem = Math.max(state.itemCount - 1, 0);

  if (key === "Escape") return { type: "close" };

  if (state.showCategoryLevel) {
    switch (key) {
      case "ArrowDown":
        return {
          type: "move",
          index: Math.min(state.activeIndex + 1, lastCat),
        };
      case "ArrowUp":
        return { type: "move", index: Math.max(state.activeIndex - 1, 0) };
      case "ArrowRight":
        if (state.categoryAttach || state.categoryDisabled) {
          return { type: "consume" };
        }
        return { type: "drill" };
      case "Enter":
      case "Tab":
        if (state.categoryAttach) return { type: "attach" };
        // disabled 一级行（空团队）落到发送，勿吞掉 Enter。
        return state.categoryDisabled ? { type: "ignore" } : { type: "drill" };
      default:
        return { type: "ignore" };
    }
  }

  if (key === "ArrowLeft" && state.canKeyBack) {
    return { type: "back" };
  }

  switch (key) {
    case "ArrowDown":
      return { type: "move", index: Math.min(state.activeIndex + 1, lastItem) };
    case "ArrowUp":
      return { type: "move", index: Math.max(state.activeIndex - 1, 0) };
    case "Enter":
    case "Tab":
      if (state.itemCount > 0) return { type: "select" };
      return state.canKeyBack ? { type: "consume" } : { type: "ignore" };
    default:
      return { type: "ignore" };
  }
}
