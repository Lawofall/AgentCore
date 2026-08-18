/**
 * 消息出口文案（仅交付 / 含过程）。各端全新建，对齐桌面 `lib/messageExport.ts`。
 *
 * `messages.content` 只留最终交付；过程时间线另有旁白与工具。复制提供两档。
 * 搜索与下轮 history 仍只用交付正文。
 */

import { toolDetail, toolLabel } from "@/components/assistantLabels";
import type { ProcessStep } from "@agentcore/contract-types";

export type MessageCopyMode = "deliverable" | "with_process";

function formatToolLine(step: Extract<ProcessStep, { kind: "tool" }>): string {
  const label = toolLabel(step.tool_name);
  const detail = toolDetail(step.arguments ?? {}, step.tool_name);
  const status =
    step.status === "error"
      ? "（失败）"
      : step.status === "running"
        ? "（进行中）"
        : "";
  return detail ? `· ${label}${status}：${detail}` : `· ${label}${status}`;
}

export function formatProcessExport(
  process: ProcessStep[] | undefined,
): string {
  if (!process?.length) return "";
  const lines: string[] = [];
  for (const step of process) {
    switch (step.kind) {
      case "reasoning": {
        const t = step.text.trim();
        if (t) lines.push(`【思考】\n${t}`);
        break;
      }
      case "content": {
        const t = step.text.trim();
        if (t) lines.push(t);
        break;
      }
      case "tool":
        lines.push(formatToolLine(step));
        break;
      case "rework":
        lines.push("· （引用/格式核验后已重写）");
        break;
      case "team":
        lines.push("· （团队协作）");
        break;
      case "checkpoint":
        lines.push("· （向你确认）");
        break;
      case "ask":
        lines.push("· （提问）");
        break;
      case "plan_review":
        lines.push("· （计划复核）");
        break;
      case "team_preview":
        lines.push("· （团队预览）");
        break;
      case "user_interjection":
        // 零宽 positional marker；正文在旁路 userInterjections，导出不复述。
        break;
      default:
        break;
    }
  }
  return lines.join("\n\n").trim();
}

/**
 * Copy/export deliverable: non-empty `content`, else empty-failure visible notice
 * (structured error / emptyFailureNotice). Does not hide content when it equals error.
 */
export function exportDeliverableText(
  content: string | null | undefined,
  failureNotice?: string | null,
): string {
  const body = (content ?? "").trim();
  if (body) return body;
  return (failureNotice ?? "").trim();
}

export function formatMessageExport(
  content: string,
  process: ProcessStep[] | undefined,
  mode: MessageCopyMode,
  opts?: { failureNotice?: string | null },
): string {
  const deliverable = exportDeliverableText(content, opts?.failureNotice);
  if (mode === "deliverable") return deliverable;

  const processText = formatProcessExport(process);
  if (!processText) return deliverable;
  if (!deliverable) return `【过程】\n\n${processText}`;

  const endsWithDeliverable =
    processText === deliverable || processText.endsWith(deliverable);
  if (endsWithDeliverable) {
    return `【过程】\n\n${processText}`;
  }
  return `【过程】\n\n${processText}\n\n【交付】\n\n${deliverable}`;
}

export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
