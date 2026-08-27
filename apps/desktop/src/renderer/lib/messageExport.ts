/**
 * 消息出口文案（仅交付 / 含过程）。
 *
 * `messages.content` 只留最终交付（deliverable_only）；气泡过程时间线另有旁白与工具。
 * 复制/分享提供两档：默认仅交付；「含过程」按 process 时序拼可读文本。
 * 搜索与下轮 history 仍只用交付正文——本模块只服务出口，不改持久化契约。
 */

import {
  channelRedirectFace,
  resolveToolWireStatus,
} from "@/lib/channelRedirect";
import { visibleMessageText } from "@/lib/errors";
import { reworkChipLabel } from "@/lib/processTimeline";
import type { ProcessStep } from "@/types/events";
import {
  MESSAGE_EXPORT_DELIVERABLE_HEADING,
  MESSAGE_EXPORT_PROCESS_HEADING,
  MESSAGE_EXPORT_REASONING_HEADING,
  MESSAGE_EXPORT_STEP_CHROME,
  MESSAGE_EXPORT_TOOL_STATUS_SUFFIX,
  type MessageCopyMode,
} from "@agentcore/protocol-fold-kit";

export type { MessageCopyMode };

/** Optional error fields for empty-failure deliverable fallback. */
export type MessageExportErrorSource = {
  error?: { message?: string } | null;
  runs?: { error?: { message?: string } | null } | null;
};

/** Chrome labels for copy text — keep in sync with message-bubble/constants TOOL_META. */
const TOOL_LABEL: Record<string, string> = {
  web_search: "Search web",
  read_url: "Read page",
  grep: "Grep code",
  code_search: "Search code",
  code_execute: "Run code",
  terminal: "Run terminal",
  test_run: "Run tests",
  git: "Git",
  file_read: "Read file",
  file_write: "Write file",
  file_append: "Append file",
  file_list: "List dir",
  glob: "Glob",
  list_folders: "List folders",
  resolve_folder: "Resolve folder",
  create_folder: "Create folder",
  delete_folder: "Delete folder",
  list_folder_dir: "List folder dir",
  read_folder_file: "Read folder file",
  str_replace: "Edit file",
  file_delete: "Delete file",
  file_move: "Move file",
  file_copy: "Copy file",
  mkdir: "Make dir",
  file_batch: "Batch files",
  write_section: "Write section",
  md_to_docx: "Export Word",
  md_to_pdf: "Export PDF",
  archive_extract: "Extract archive",
  download_url: "Download file",
  read_image: "Read image",
  code_diagnostics: "Check types",
  delegate: "Delegate",
  replan: "Replan",
  debate: "Debate",
  ask_user: "Ask you",
  consult_skill: "Consult skill",
  consult_memory: "Consult memory",
  consult_rule: "Consult rule",
  consult: "Consult",
  remember: "Remember",
  update_folder_profile: "Update folder profile",
  search_conversations: "Search conversations",
  read_conversation: "Read conversation",
  revise: "Revise",
  escalate: "Escalate",
  update_synthesis: "Update synthesis",
  cancel_worker: "Cancel worker",
  resolve_escalation: "Resolve escalate",
  queue_user_message: "Queue message",
  wait: "Wait",
  post_note: "Post note",
  read_notes: "Read notes",
  amend_note: "Amend note",
  handoff: "Handoff",
  board_ops: "Edit board",
  board_read: "Read board",
  desktop_notify: "Notify",
  external_mount_readonly: "Mount folder",
  browser: "Browser",
  browser_navigate: "Navigate",
  browser_click: "Click",
  browser_type: "Type",
  browser_scroll: "Scroll",
  browser_snapshot: "Snapshot",
  browser_screenshot: "Screenshot",
  browser_console: "Console",
  host: "Host",
  host_ping: "Host ping",
  host_info: "Host info",
  host_audio_devices: "Audio devices",
  host_storage: "Host storage",
  host_power: "Host power",
  host_network_summary: "Network summary",
  host_apps: "Host apps",
  host_os_log_summary: "OS log summary",
  host_shell: "Host shell",
  host_open_settings: "Open settings",
  host_audio_set_default: "Set default audio",
  host_service_restart: "Restart service",
  host_package_install: "Install package",
};

const TOOL_DETAIL_KEYS = [
  "query",
  "url",
  "pattern",
  "path",
  "directory",
  "command",
  "code",
  "q",
  "name", // consult / consult_* / create_folder
  "text",
] as const;

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** `id` / `*_id` 是内部标识：复制出去的过程稿同样不摆（与工具行标题同一条纪律）。 */
function isInternalIdArg(key: string): boolean {
  return key === "id" || key.endsWith("_id");
}

function skipExportChip(key: string, raw: string): boolean {
  const v = raw.trim();
  if (!v || v === ".") return true;
  if (isInternalIdArg(key)) return true;
  if (UUID_RE.test(v)) return true;
  return false;
}

function browserExportDetail(args: Record<string, unknown>): string {
  const action = typeof args.action === "string" ? args.action.trim() : "";
  if (action === "navigate") {
    return typeof args.url === "string" ? args.url.trim() : action;
  }
  if (action === "click") {
    return typeof args.ref === "string" ? args.ref.trim() : action;
  }
  if (action === "type") {
    const text = typeof args.text === "string" ? args.text.trim() : "";
    return text || action;
  }
  if (action === "scroll") {
    return typeof args.dy === "number" ? `${args.dy}px` : action;
  }
  if (action) return action;
  return typeof args.url === "string" ? args.url.trim() : "";
}

function hostExportDetail(args: Record<string, unknown>): string {
  const action = typeof args.action === "string" ? args.action.trim() : "";
  if (action === "shell") {
    return typeof args.command === "string" ? args.command.trim() : action;
  }
  if (action === "install_package") {
    const manager =
      typeof args.manager === "string" && args.manager.trim()
        ? args.manager.trim()
        : "";
    const pkg =
      typeof args.package_id === "string" && args.package_id.trim()
        ? args.package_id.trim()
        : "";
    const cask = args.cask === true ? " (cask)" : "";
    if (manager && pkg) return `${manager} ${pkg}${cask}`;
    return pkg || manager || action;
  }
  if (action) return action;
  return "";
}

function gitExportDetail(args: Record<string, unknown>): string {
  const sub = typeof args.subcommand === "string" ? args.subcommand.trim() : "";
  return sub;
}

function toolDetail(args: Record<string, unknown>, toolName?: string): string {
  // WaitTool.reason 仅记日志，复制稿同样不摆。
  if (toolName === "wait") return "";
  if (toolName === "browser") {
    const browser = browserExportDetail(args);
    if (browser) return browser;
  }
  if (toolName === "host") {
    const host = hostExportDetail(args);
    if (host) return host;
  }
  if (toolName === "git") {
    const git = gitExportDetail(args);
    if (git) return git;
  }
  for (const k of TOOL_DETAIL_KEYS) {
    const v = args[k];
    if (typeof v === "string" && v.trim() && !skipExportChip(k, v)) {
      return v.trim();
    }
  }
  for (const [k, v] of Object.entries(args)) {
    if (typeof v !== "string" || !v.trim()) continue;
    if (skipExportChip(k, v)) continue;
    return v.trim();
  }
  return "";
}

function browserExportLabel(args: Record<string, unknown>): string {
  const action = typeof args.action === "string" ? args.action.trim() : "";
  if (action === "navigate") return "Navigate";
  if (action === "click") return "Click";
  if (action === "type") return "Type";
  if (action === "scroll") return "Scroll";
  if (action === "snapshot") return "Snapshot";
  if (action === "screenshot") return "Screenshot";
  if (action === "console") return "Console";
  return action ? action.charAt(0).toUpperCase() + action.slice(1) : "Browser";
}

function hostExportLabel(args: Record<string, unknown>): string {
  const action = typeof args.action === "string" ? args.action.trim() : "";
  if (action === "status") return "Host status";
  if (action === "os_log") return "OS log summary";
  if (action === "shell") return "Host shell";
  if (action === "open_settings") return "Open settings";
  if (action === "set_audio") return "Set default audio";
  if (action === "restart_service") return "Restart service";
  if (action === "install_package") return "Install package";
  return action ? `Host ${action}` : "Host";
}

function formatToolLine(step: Extract<ProcessStep, { kind: "tool" }>): string {
  const wire = resolveToolWireStatus(step.status, step.failure);
  const redirect = channelRedirectFace(step.failure?.code);
  const label =
    wire === "redirect" && redirect
      ? redirect.label
      : step.tool_name === "browser"
        ? browserExportLabel(step.arguments ?? {})
        : step.tool_name === "host"
          ? hostExportLabel(step.arguments ?? {})
          : (TOOL_LABEL[step.tool_name] ?? step.tool_name);
  const detail =
    wire === "redirect" ? "" : toolDetail(step.arguments ?? {}, step.tool_name);
  const status =
    wire === "error"
      ? MESSAGE_EXPORT_TOOL_STATUS_SUFFIX.error
      : wire === "running"
        ? MESSAGE_EXPORT_TOOL_STATUS_SUFFIX.running
        : "";
  return detail ? `· ${label}${status}：${detail}` : `· ${label}${status}`;
}

/** Format the turn's process timeline into plain readable text (旁白 + 关键工具). */
export function formatProcessExport(
  process: ProcessStep[] | undefined,
  isStreaming = false,
): string {
  if (!process?.length) return "";
  const lines: string[] = [];
  for (let i = 0; i < process.length; i++) {
    const step = process[i];
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
      case "rework": {
        const hasContentAfter = process
          .slice(i + 1)
          .some((s) => s.kind === "content");
        lines.push(`· （${reworkChipLabel(isStreaming, hasContentAfter)}）`);
        break;
      }
      case "team":
        lines.push(MESSAGE_EXPORT_STEP_CHROME.team);
        break;
      case "checkpoint":
        lines.push(MESSAGE_EXPORT_STEP_CHROME.checkpoint);
        break;
      case "plan_review":
        lines.push(MESSAGE_EXPORT_STEP_CHROME.plan_review);
        break;
      default:
        break;
    }
  }
  return lines.join("\n\n").trim();
}

/**
 * Build clipboard / share text for an assistant message.
 * - deliverable: `messages.content` only（默认）；纯失败（空 content）回落 error.message
 * - with_process: 过程时间线 + 交付正文（无 process 时退化为仅交付）
 */
export function formatMessageExport(
  content: string,
  process: ProcessStep[] | undefined,
  mode: MessageCopyMode,
  errorSource?: MessageExportErrorSource,
  isStreaming = false,
): string {
  const deliverable = visibleMessageText({
    content,
    error: errorSource?.error,
    runs: errorSource?.runs,
  });
  if (mode === "deliverable") return deliverable;

  const processText = formatProcessExport(process, isStreaming);
  if (!processText) return deliverable;
  if (!deliverable)
    return `${MESSAGE_EXPORT_PROCESS_HEADING}\n\n${processText}`;

  // Trailing content steps often already equal the deliverable; avoid duplicating
  // the final answer when the timeline already ends on it.
  const endsWithDeliverable =
    processText === deliverable || processText.endsWith(deliverable);
  if (endsWithDeliverable) {
    return `${MESSAGE_EXPORT_PROCESS_HEADING}\n\n${processText}`;
  }
  return `${MESSAGE_EXPORT_PROCESS_HEADING}\n\n${processText}\n\n${MESSAGE_EXPORT_DELIVERABLE_HEADING}\n\n${deliverable}`;
}
