/**
 * 消息出口文案（仅交付 / 含过程）。各端全新建，对齐桌面 `lib/messageExport.ts`。
 *
 * `messages.content` 只留最终交付；过程时间线另有旁白与工具。复制提供两档。
 * 搜索与下轮 history 仍只用交付正文。
 */

import { toolDetail, toolLabel } from "@/components/assistantLabels";
import type { ProcessStep } from "@agentcore/contract-types";
import {
  MESSAGE_EXPORT_DELIVERABLE_HEADING,
  MESSAGE_EXPORT_PROCESS_HEADING,
  MESSAGE_EXPORT_REASONING_HEADING,
  MESSAGE_EXPORT_STEP_CHROME,
  MESSAGE_EXPORT_TOOL_STATUS_SUFFIX,
  type MessageCopyMode,
} from "@agentcore/protocol-fold-kit";

export type { MessageCopyMode };

function formatToolLine(step: Extract<ProcessStep, { kind: "tool" }>): string {
  const label = toolLabel(step.tool_name);
  const detail = toolDetail(step.arguments ?? {}, step.tool_name);
  const status =
    step.status === "error"
      ? MESSAGE_EXPORT_TOOL_STATUS_SUFFIX.error
      : step.status === "running"
        ? MESSAGE_EXPORT_TOOL_STATUS_SUFFIX.running
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
        if (t) lines.push(`${MESSAGE_EXPORT_REASONING_HEADING}\n${t}`);
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
        lines.push(MESSAGE_EXPORT_STEP_CHROME.team);
        break;
      case "checkpoint":
        lines.push(MESSAGE_EXPORT_STEP_CHROME.checkpoint);
        break;
      case "ask":
        lines.push(MESSAGE_EXPORT_STEP_CHROME.ask);
        break;
      case "plan_review":
        lines.push(MESSAGE_EXPORT_STEP_CHROME.plan_review);
        break;
      case "team_preview":
        lines.push(MESSAGE_EXPORT_STEP_CHROME.team_preview);
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
  if (!deliverable)
    return `${MESSAGE_EXPORT_PROCESS_HEADING}\n\n${processText}`;

  const endsWithDeliverable =
    processText === deliverable || processText.endsWith(deliverable);
  if (endsWithDeliverable) {
    return `${MESSAGE_EXPORT_PROCESS_HEADING}\n\n${processText}`;
  }
  return `${MESSAGE_EXPORT_PROCESS_HEADING}\n\n${processText}\n\n${MESSAGE_EXPORT_DELIVERABLE_HEADING}\n\n${deliverable}`;
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
