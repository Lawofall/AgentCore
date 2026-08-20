// Shared chrome labels for assistant rendering (English tool names / 上下文通道名 + the arg-detail
// picker). Extracted so BOTH the inline timeline ({@link AssistantView}) and the per-worker
// run-detail panel ({@link TeamView} · RunDetail) read ONE source — the maps are the kind of
// chrome that silently drifts if copied. Pure data + string helpers, no JSX, so it stays a
// leaf both can import without an import cycle (AssistantView → TeamView → RunDetail → here).
//
// These mirror the desktop `TOOL_META` / `CONTEXT_CHANNEL_META` labels so the two ends read the
// same (各端全新建 per cross-platform-frontend; labels are chrome, NOT shared business logic).

import type { ToolPhase, WorkerRunPhase } from "@agentcore/contract-types";

/** Context channel → 中文 label (上下文传递可视化). Covers the CEO-side opening channels
 *  (系统提示 / 对话历史 / 原始请求 / 队员回传) and the worker-side / 续写 channels (任务 / 交付物 /
 *  前置结果 / 本轮焦点 …). An unknown channel falls back to its raw name. */
export const CONTEXT_CHANNEL_LABEL: Record<string, string> = {
  system: "系统提示",
  history: "对话历史",
  request: "原始请求",
  team_position: "团队位置",
  dependency: "前置结果",
  workspace: "工作区",
  task: "你的任务",
  deliverable: "交付物规格",
  team_brief: "团队共识",
  gate_notes: "把关要点",
  steer: "中途指示",
  team_result: "队员回传",
  round_focus: "本轮焦点",
  opponent: "对方论点",
  challenge: "被驳命门",
  interjection: "你的追问",
  continuation: "接续指令",
  cross_exam: "质询",
  witness_exam: "证人",
  closing: "结辩",
};

/** English tool labels — mirror desktop `TOOL_META` so both ends read the same
 *  (各端全新建 per cross-platform-frontend; labels are chrome, NOT shared business logic).
 *  An unknown tool falls back to its raw backend name. */
const TOOL_LABEL: Record<string, string> = {
  web_search: "Search web",
  read_url: "Read page",
  grep: "Grep code",
  code_execute: "Run code",
  file_read: "Read file",
  file_write: "Write file",
  file_append: "Append file",
  file_list: "List dir",
  str_replace: "Edit file",
  file_delete: "Delete file",
  file_move: "Move file",
  file_copy: "Copy file",
  mkdir: "Make dir",
  file_batch: "Batch files",
  delegate: "Delegate",
  ask_user: "Ask you",
  consult_skill: "Consult skill",
  consult_memory: "Consult memory",
  consult_rule: "Consult rule",
  consult: "Consult",
  revise: "Revise",
  escalate: "Escalate",
  handoff: "Handoff",
  wait: "Wait",
  host: "Host",
  // 历史会话回放：旧 host_* 键只供展示。
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

const HOST_ACTION_LABEL: Record<string, string> = {
  status: "Host status",
  os_log: "OS log summary",
  shell: "Host shell",
  open_settings: "Open settings",
  set_audio: "Set default audio",
  restart_service: "Restart service",
  install_package: "Install package",
};

function hostActionOf(args?: Record<string, unknown>): string {
  return args && typeof args.action === "string" ? args.action.trim() : "";
}

export const toolLabel = (
  name: string,
  args?: Record<string, unknown>,
): string => {
  if (name === "host") {
    const action = hostActionOf(args);
    if (action) return HOST_ACTION_LABEL[action] ?? `Host ${action}`;
    return "Host";
  }
  return TOOL_LABEL[name] ?? name;
};

/** 工具行右侧生命周期，与桌面勾/失败标同一语义；手机写汉字以免 Done 挤死窄行。 */
export const TOOL_STATUS_LABEL = {
  running: "进行中",
  success: "完成",
  error: "失败",
} as const;

/** 工具执行阶段 → 等待态文案（联网 UX）。桌面 `toolPhaseText` 仍是英文；手机跟生命周期一样写汉字。 */
const TOOL_PHASE_TEXT: Record<ToolPhase, string> = {
  queued: "排队中",
  querying: "正在检索",
  fallback: "改用备用",
  fetching: "正在打开页面",
  reading: "正在提取",
  executing: "进行中",
  blocked: "网络不可用",
  git_queued: "等待仓库",
  git_credentials: "核对凭据",
  git_remote: "连接远端",
};

export function toolPhaseText(phase: string | undefined): string | null {
  if (!phase) return null;
  return TOOL_PHASE_TEXT[phase as ToolPhase] ?? TOOL_STATUS_LABEL.running;
}

/** 读文件触顶 / 验证预算等「不是失败」的工具行状态。 */
export const TOOL_GUIDANCE_LABEL = "提示";

/** Worker mid-flight `run.phase` → badge copy (SSE `run_phase`).
 *  queued = status pending →「排队中」; skipped = status skipped →「未执行」(caller).
 *  Absent phase on running → null (caller keeps generic「进行中」). */
const RUN_PHASE_LABEL: Record<WorkerRunPhase, string> = {
  thinking: "思考中",
  tool: "工具中",
  waiting_children: "等待子任务",
  winding_down: "收尾中",
};

export function runPhaseLabel(
  phase: WorkerRunPhase | null | undefined,
): string | null {
  if (!phase) return null;
  return RUN_PHASE_LABEL[phase] ?? null;
}

/** The most descriptive string arg to show beside a tool (its query / url / path / …);
 *  empty when the call carries no representative string arg. */
const TOOL_DETAIL_KEYS = [
  "query",
  "url",
  "pattern",
  "path",
  "command",
  "code",
  "q",
  "name", // consult / consult_*
  "text",
];

/** `id` / `*_id` 是内部标识（run_id / conversation_id / interjection_id …）：不进用户面。
 *  用户在协作图上认的是角色名，`撤回队员 r-a3f2e1c8-…` 只会让他放弃对账。 */
function isInternalIdArg(key: string): boolean {
  return key === "id" || key.endsWith("_id");
}

function hostToolDetail(args: Record<string, unknown>): string {
  const action = hostActionOf(args);
  if (action === "shell") {
    return typeof args.command === "string" ? args.command.trim() : "";
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
  if (action === "open_settings") {
    return typeof args.panel === "string" ? args.panel.trim() : action;
  }
  if (action === "set_audio") {
    const name =
      typeof args.device_name === "string" && args.device_name.trim()
        ? args.device_name.trim()
        : typeof args.device_id === "string"
          ? args.device_id.trim()
          : "";
    return name || action;
  }
  if (action === "restart_service") {
    return typeof args.service === "string" ? args.service.trim() : action;
  }
  if (action === "os_log") {
    return typeof args.source === "string" ? args.source.trim() : action;
  }
  if (action === "status") {
    const facets = args.facets;
    if (Array.isArray(facets)) {
      const names = facets
        .filter((f): f is string => typeof f === "string" && f.trim().length > 0)
        .map((f) => f.trim());
      if (names.length > 0) return names.join(", ");
    }
    return "";
  }
  return action;
}

export function toolDetail(
  args: Record<string, unknown>,
  toolName?: string,
): string {
  // WaitTool.reason 仅记日志；画进标题会变成「右边已完成、中间还说仍在跑」。
  if (toolName === "wait") return "";
  if (toolName === "host") return hostToolDetail(args);
  for (const k of TOOL_DETAIL_KEYS) {
    const v = args[k];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  for (const [k, v] of Object.entries(args)) {
    if (isInternalIdArg(k)) continue;
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}
