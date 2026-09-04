/** Human-readable section titles for prompt XML tags (server-side section markers). */
export const PROMPT_TAG_LABELS: Record<string, string> = {
  output_style: "输出风格",
  tool_use: "工具使用",
  system_feedback: "系统反馈",
  runtime_context: "运行时上下文",
  citing_sources: "引用规范",
  visualization: "可视化",
  role: "角色",
  how_you_work: "工作方式",
  platform_knowledge: "平台知识边界",
  rules: "长期记忆",
  能力目录: "能力目录",
  记忆主题目录: "记忆主题目录",
  workspace_file_index: "工作区文件索引",
  tool_safety: "工具安全",
  team_orchestration_advanced: "团队编排进阶",
  debate_and_review: "辩论与交叉审查",
  asking_the_user: "向用户提问",
  legal_answer_brief: "法律答复要点",
  legal_case_analysis: "法律案情分析",
};

/** Resolve a prompt section tag to a display title. */
export function labelForPromptTag(tag: string): string {
  const known = PROMPT_TAG_LABELS[tag];
  if (known) return known;
  return tag.replace(/_/g, " ");
}
