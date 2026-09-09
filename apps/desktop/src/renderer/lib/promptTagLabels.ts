/** Human-readable section titles for prompt XML tags (server-side section markers). */
export const PROMPT_TAG_LABELS: Record<string, string> = {
  身份: "身份",
  输入: "输入",
  工作权威: "工作权威",
  诚实: "诚实",
  输出: "输出",
  设定: "设定",
  工作区: "工作区",
  按需目录: "按需目录",
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
  staffing: "团队拆法",
  lead_subteam: "子队拆法",
  local_desk: "本机目录进工作区",
  delivery: "交付环境",
  debate_and_review: "辩论与交叉审查",
  ask_kickoff: "开场提问",
  ask_midtask: "途中提问",
  page_ui: "页面观感",
  legal_answer_brief: "民事答辩状",
  legal_complaint: "民事起诉状",
  legal_case_analysis: "接案评估与诉讼策略",
  legal_contract_review: "合同审查",
};

/** Resolve a prompt section tag to a display title. */
export function labelForPromptTag(tag: string): string {
  const known = PROMPT_TAG_LABELS[tag];
  if (known) return known;
  return tag.replace(/_/g, " ");
}
