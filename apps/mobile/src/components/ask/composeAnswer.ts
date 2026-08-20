/**
 * Compose picks + free note into ONE readable answer (答复模型 α).
 * Mobile-local; mirrors desktop AskUserFields.composeAnswer.
 */

export type ComposeQuestion = {
  id: string;
  prompt: string;
  default?: string | null;
};

export function composeAnswer(
  questions: ComposeQuestion[],
  answers: Record<string, string[]>,
  note: string,
): string {
  const trimmed = note.trim();
  if (questions.length === 0) return trimmed;
  const lines: string[] = [];
  for (const q of questions) {
    const picked = (answers[q.id] ?? []).map((s) => s.trim()).filter(Boolean);
    if (picked.length) lines.push(`· ${q.prompt}：${picked.join("、")}`);
    else if (q.default && !trimmed) lines.push(`· ${q.prompt}：（按你的默认）`);
  }
  if (trimmed) lines.push(`· 补充：${trimmed}`);
  if (lines.length === 0) return trimmed;
  return ["我的答复：", ...lines].join("\n");
}
