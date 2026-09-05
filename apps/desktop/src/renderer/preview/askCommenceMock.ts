import type { AskUserContent } from "@/components/chat/ask/AskUserFields";

/**
 * Shared mock for retired ask-commence layout preview variants.
 * Rich ask_user payload (questions) — production mounts
 * {@link AskDecisionBody} (scene v5), not a kickoff ceremony shell.
 */
export const ASK_COMMENCE_MOCK: AskUserContent = {
  question:
    "按这版起步计划开做可以吗？有两处想先跟你对齐。\n需求能做，但方向还差两处对齐。\n先按可执行起步计划开做\n确认后立刻动手，途中可再改",
  questions: [
    {
      id: "q0",
      prompt: "主要给谁看？",
      kind: "choice",
      options: [
        {
          label: "潜在客户（推荐）",
          detail: "偏转化：卖点清晰、CTA 突出",
        },
        { label: "投资人", detail: "偏叙事：愿景与里程碑优先" },
        { label: "内部评审", detail: "偏完整：信息密度更高" },
      ],
      multiple: false,
      default: "潜在客户（推荐）",
    },
    {
      id: "q1",
      prompt: "首版要不要双语？",
      kind: "choice",
      options: [
        { label: "只要中文（推荐）" },
        { label: "中英双语", detail: "文案量约翻倍，首版会慢半拍" },
      ],
      multiple: false,
      default: "只要中文（推荐）",
    },
  ],
};

export type AskCommenceVariantId =
  | "ask-commence-v1"
  | "ask-commence-v2"
  | "ask-commence-v3"
  | "ask-commence-v4"
  | "ask-commence-v5";

export interface AskCommenceScene {
  id: AskCommenceVariantId;
  /** Short label in the scene list. */
  title: string;
  /** One-line design intent for the product owner. */
  intent: string;
  /** Industry paradigm this layout borrows from. */
  paradigm: string;
}
