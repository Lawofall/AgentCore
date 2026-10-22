export type OnboardingPreviewScene = {
  id: string;
  title: string;
  intent: string;
  /** Draft empty-state or composer generating variant. */
  kind:
    | "empty-draft"
    | "composer-generating-bar"
    | "composer-generating-card";
};

export const ONBOARDING_PREVIEW_SCENES: readonly OnboardingPreviewScene[] = [
  {
    id: "empty-draft",
    title: "空态",
    intent: "单句问候 + 输入框居中成一体",
    kind: "empty-draft",
  },
  {
    id: "composer-generating-bar",
    title: "生成中 · 底部条排队",
    intent: "回合执行中：有草稿时插队/排队/停止并存（bar）",
    kind: "composer-generating-bar",
  },
  {
    id: "composer-generating-card",
    title: "生成中 · 居中输入排队",
    intent: "回合执行中：聊天居中输入插队/排队/停止并存（card）",
    kind: "composer-generating-card",
  },
];
