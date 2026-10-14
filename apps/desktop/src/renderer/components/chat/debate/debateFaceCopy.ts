/**
 * 协作图辩论节点卡片文案：从 run_context 抽「本轮焦点 / 任务首句 / 被驳命门」，
 * 替代完成态误导性开团 role 模板（`run.task`）。
 */

/** 取首句并截断，供卡片一行扫读。 */
export function firstSentence(text: string, maxLen = 80): string {
  const t = text.trim().replace(/\s+/g, " ");
  if (!t) return "";
  const m = t.match(/^(.+?[。！？.!?\n])/);
  const sentence = (m?.[1] ?? t).trim();
  if (sentence.length <= maxLen) return sentence;
  return `${sentence.slice(0, Math.max(1, maxLen - 1))}…`;
}

type ContextBlock = { channel: string; body: string };

/**
 * 辩论续轮卡片主文：优先升格后的 `task` 块首句，再 `round_focus`。
 * 无块 → null（调用方勿回落到开团 role 模板）。
 */
export function debateFacePrimaryFromContext(
  blocks: ReadonlyArray<ContextBlock> | null | undefined,
): string | null {
  if (!blocks?.length) return null;
  const task = blocks.find((b) => b.channel === "task");
  if (task?.body?.trim()) return firstSentence(task.body);
  const focus = blocks.find((b) => b.channel === "round_focus");
  if (focus?.body?.trim()) return firstSentence(focus.body);
  return null;
}

/** 被驳命门副标题（`channel=challenge`）。 */
export function challengePreviewFromContext(
  blocks: ReadonlyArray<ContextBlock> | null | undefined,
): string | null {
  if (!blocks?.length) return null;
  const block = blocks.find((b) => b.channel === "challenge");
  const text = block?.body?.trim();
  if (!text) return null;
  return firstSentence(text, 60);
}

const TERMINAL = new Set(["completed", "failed", "cancelled", "skipped", "cut"]);

/**
 * 卡片主文优先级：完成态产出预览 → 辩论续轮焦点/任务首句 → 非辩论/进行中 task。
 * 辩论完成态无产出且无焦点时返回 null（宁空不显示开团模板）。
 */
export function pickAgentNodeIdlePrimary(input: {
  status: string;
  outputPreview: string;
  task: string;
  isDebate: boolean;
  debateFacePrimary?: string | null;
}): string | null {
  const terminal = TERMINAL.has(input.status);
  if (terminal && input.outputPreview.trim()) {
    return input.outputPreview.trim();
  }
  if (input.isDebate) {
    if (input.debateFacePrimary?.trim()) return input.debateFacePrimary.trim();
    if (terminal) return null;
  }
  const task = input.task.trim();
  return task || null;
}
