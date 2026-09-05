import {
  channelRedirectFace,
  resolveToolWireStatus,
} from "@/lib/channelRedirect";
import { formatCompact } from "@/lib/format";
import type { ProcessStep, ToolPhase } from "@/types/events";
import {
  ArrowUp,
  Bell,
  BookOpen,
  Brain,
  Camera,
  Clock,
  Code2,
  Compass,
  FileText,
  Folder,
  Forward,
  Gavel,
  GitBranch,
  Globe,
  HardDrive,
  HelpCircle,
  Inbox,
  Keyboard,
  LayoutGrid,
  ListRestart,
  type LucideIcon,
  MessagesSquare,
  Monitor,
  MousePointerClick,
  MoveVertical,
  Network,
  NotebookPen,
  Package,
  PenLine,
  Pencil,
  Presentation,
  Scale,
  ScanText,
  ScrollText,
  Search,
  Settings2,
  Terminal,
  TestTube2,
  Trash2,
  UserX,
  Users,
  Volume2,
  Wrench,
  Zap,
} from "lucide-react";

/** Icon + English action label for a builtin tool, by its backend name. */
export const TOOL_META: Record<string, { Icon: LucideIcon; label: string }> = {
  web_search: { Icon: Search, label: "Search web" },
  web_fetch: { Icon: Globe, label: "Read page" },
  grep: { Icon: Code2, label: "Grep code" },
  code_search: { Icon: Code2, label: "Search code" },
  run: { Icon: Terminal, label: "Run" },
  code_execute: { Icon: Terminal, label: "Run code" },
  terminal: { Icon: Terminal, label: "Run terminal" },
  test_run: { Icon: TestTube2, label: "Run tests" },
  git: { Icon: GitBranch, label: "Git" },
  file_read: { Icon: FileText, label: "Read file" },
  file_write: { Icon: FileText, label: "Write file" },
  file_append: { Icon: FileText, label: "Append file" },
  file_list: { Icon: Folder, label: "List dir" },
  glob: { Icon: Folder, label: "Glob" },
  list_folders: { Icon: Folder, label: "List folders" },
  resolve_folder: { Icon: Folder, label: "Resolve folder" },
  create_folder: { Icon: Folder, label: "Create folder" },
  delete_folder: { Icon: Trash2, label: "Delete folder" },
  list_folder_dir: { Icon: Folder, label: "List folder dir" },
  read_folder_file: { Icon: FileText, label: "Read folder file" },
  str_replace: { Icon: Pencil, label: "Edit file" },
  file_delete: { Icon: Trash2, label: "Delete file" },
  file_move: { Icon: FileText, label: "Move file" },
  file_copy: { Icon: FileText, label: "Copy file" },
  mkdir: { Icon: FileText, label: "Make dir" },
  file_batch: { Icon: FileText, label: "Batch files" },
  md_to_docx: { Icon: FileText, label: "Export Word" },
  md_to_pdf: { Icon: FileText, label: "Export PDF" },
  archive_extract: { Icon: Package, label: "Extract archive" },
  archive_create: { Icon: Package, label: "Create archive" },
  download_url: { Icon: Globe, label: "Download file" },
  read_image: { Icon: ScanText, label: "Read image" },
  code_diagnostics: { Icon: Code2, label: "Check types" },
  delegate: { Icon: Users, label: "Delegate" },
  replan: { Icon: ListRestart, label: "Replan" },
  // CEO 编排原语（组队辩论）：气泡侧只在参数组装心跳时露出，
  // 图标与开工卡的 debate 形态一致（Scale）。
  debate: { Icon: Scale, label: "Debate" },
  ask_user: { Icon: HelpCircle, label: "Ask you" },
  consult_skill: { Icon: BookOpen, label: "Consult skill" },
  consult_memory: { Icon: Brain, label: "Consult memory" },
  consult_rule: { Icon: BookOpen, label: "Consult rule" },
  // 按需三合一：新会话走单一 consult；旧三名上表仍保留供历史回放。
  consult: { Icon: BookOpen, label: "Consult" },
  remember: { Icon: BookOpen, label: "Remember" },
  update_folder_profile: { Icon: NotebookPen, label: "Update folder profile" },
  // Worker-only 跨会话对话日志（CEO 经 delegate 派查阅员）。
  search_conversations: { Icon: MessagesSquare, label: "Search conversations" },
  read_conversation: { Icon: MessagesSquare, label: "Read conversation" },
  revise: { Icon: PenLine, label: "Revise" },
  escalate: { Icon: ArrowUp, label: "Escalate" },
  // CEO 协调模式原语（波内边跑边调）：与 file/web 工具同走 ToolLine。
  update_synthesis: { Icon: NotebookPen, label: "Update synthesis" },
  cancel_worker: { Icon: UserX, label: "Cancel worker" },
  resolve_escalation: { Icon: Gavel, label: "Resolve escalate" },
  queue_user_message: { Icon: Inbox, label: "Queue message" },
  wait: { Icon: Clock, label: "Wait" },
  // L3 团队浏览器（单工具 `browser`，按 action 展示；同构 host）
  browser: { Icon: Globe, label: "Browser" },
  // 历史会话回放：旧 browser_* 七键只供展示，不注册、不转发。
  browser_navigate: { Icon: Compass, label: "Navigate" },
  browser_click: { Icon: MousePointerClick, label: "Click" },
  browser_type: { Icon: Keyboard, label: "Type" },
  browser_scroll: { Icon: MoveVertical, label: "Scroll" },
  browser_snapshot: { Icon: ScanText, label: "Snapshot" },
  browser_screenshot: { Icon: Camera, label: "Screenshot" },
  browser_console: { Icon: ScrollText, label: "Console" },
  // Worker / board channels that also render on the process timeline.
  handoff: { Icon: Forward, label: "Handoff" },
  board_ops: { Icon: Presentation, label: "Edit board" },
  board_read: { Icon: Presentation, label: "Read board" },
  desktop_notify: { Icon: Bell, label: "Notify" },
  external_mount_readonly: { Icon: Folder, label: "Mount folder" },
  // 本机 Host（第三能力面 · 单工具 `host`，按 action 展示；同构 git + subcommand）
  host: { Icon: Monitor, label: "Host" },
  // 历史会话回放：旧 host_* 键只供展示，不注册、不转发。
  host_ping: { Icon: Monitor, label: "Host ping" },
  host_info: { Icon: Monitor, label: "Host info" },
  host_audio_devices: { Icon: Volume2, label: "Audio devices" },
  host_storage: { Icon: HardDrive, label: "Host storage" },
  host_power: { Icon: Zap, label: "Host power" },
  host_network_summary: { Icon: Network, label: "Network summary" },
  host_apps: { Icon: LayoutGrid, label: "Host apps" },
  host_os_log_summary: { Icon: ScrollText, label: "OS log summary" },
  host_shell: { Icon: Terminal, label: "Host shell" },
  host_open_settings: { Icon: Settings2, label: "Open settings" },
  host_audio_set_default: { Icon: Volume2, label: "Set default audio" },
  host_service_restart: { Icon: ListRestart, label: "Restart service" },
  host_package_install: { Icon: Package, label: "Install package" },
};

/** `browser` 的 action → 过程行标签 / 图标（对齐旧 browser_* chrome，便于对照历史会话）。 */
const BROWSER_ACTION_META: Record<string, { Icon: LucideIcon; label: string }> =
  {
    navigate: { Icon: Compass, label: "Navigate" },
    click: { Icon: MousePointerClick, label: "Click" },
    type: { Icon: Keyboard, label: "Type" },
    scroll: { Icon: MoveVertical, label: "Scroll" },
    snapshot: { Icon: ScanText, label: "Snapshot" },
    screenshot: { Icon: Camera, label: "Screenshot" },
    console: { Icon: ScrollText, label: "Console" },
  };

function browserActionOf(args?: Record<string, unknown>): string {
  const raw = args && typeof args.action === "string" ? args.action.trim() : "";
  return raw;
}

/** `host` 的 action → 过程行标签 / 图标（对齐旧 host_* chrome，便于对照历史会话）。 */
const HOST_ACTION_META: Record<string, { Icon: LucideIcon; label: string }> = {
  status: { Icon: Monitor, label: "Host status" },
  os_log: { Icon: ScrollText, label: "OS log summary" },
  shell: { Icon: Terminal, label: "Host shell" },
  open_settings: { Icon: Settings2, label: "Open settings" },
  set_audio: { Icon: Volume2, label: "Set default audio" },
  restart_service: { Icon: ListRestart, label: "Restart service" },
  install_package: { Icon: Package, label: "Install package" },
};

function hostActionOf(args?: Record<string, unknown>): string {
  const raw = args && typeof args.action === "string" ? args.action.trim() : "";
  return raw;
}

/** Tool-group collapse summaries reuse {@link TOOL_META} English labels (same chrome as ToolLine). */
const toolSummaryLabel = (
  name: string,
  args?: Record<string, unknown>,
): string => toolMeta(name, args).label;

/** 写盘家族：折叠标题已点名路径；组装心跳只在这类工具上报字数（长写入不能看起来冻住）。 */
export const WRITE_FAMILY_TOOLS = new Set([
  "file_write",
  "file_append",
  "str_replace",
]);

/** 组装心跳字数；非写盘或尚未出字返回 null。 */
export function composingWriteChars(
  toolName: string,
  chars: number,
): string | null {
  if (chars <= 0 || !WRITE_FAMILY_TOOLS.has(toolName)) return null;
  return `${formatCompact(chars)} 字`;
}

export const toolMeta = (
  name: string,
  args?: Record<string, unknown>,
): { Icon: LucideIcon; label: string } => {
  if (name === "browser") {
    const action = browserActionOf(args);
    if (action) {
      return (
        BROWSER_ACTION_META[action] ?? {
          Icon: Globe,
          label: action.charAt(0).toUpperCase() + action.slice(1),
        }
      );
    }
    return TOOL_META.browser ?? { Icon: Globe, label: "Browser" };
  }
  if (name === "host") {
    const action = hostActionOf(args);
    if (action) {
      return (
        HOST_ACTION_META[action] ?? {
          Icon: Monitor,
          label: `Host ${action}`,
        }
      );
    }
    return TOOL_META.host ?? { Icon: Monitor, label: "Host" };
  }
  return TOOL_META[name] ?? { Icon: Wrench, label: name };
};

/** Tool execution phase → waiting-state chrome (network UX): a running tool's coarse phase
 * (from a `tool_use_progress` event) as user-facing text — a slow builtin fires these while its
 * blocking leg is in flight so the waiting row is live instead of a dead spinner. web_search:
 * Searching / Queued / Trying fallback; web_fetch: Fetching page / Extracting; code_execute: Running.
 * git can wait ~2min, and each of its waits names itself: queued behind another write on the same
 * repo, resolving credentials, on the remote round trip, or running the local command. */
const TOOL_PHASE_TEXT: Record<ToolPhase, string> = {
  queued: "Queued",
  querying: "Searching",
  fallback: "Trying fallback",
  fetching: "Fetching page",
  reading: "Extracting",
  executing: "Running",
  blocked: "Network blocked",
  git_queued: "Waiting for repo",
  git_credentials: "Checking credentials",
  git_remote: "Contacting remote",
};

/** Waiting-state text for a running tool step's `phase`, or null when it has none yet.
 * An unrecognized (newer-backend) phase degrades to a generic "Working" rather than vanishing. */
export function toolPhaseText(phase: string | undefined): string | null {
  if (!phase) return null;
  return TOOL_PHASE_TEXT[phase as ToolPhase] ?? "Working";
}

/** Locator args — safe to chip into the collapsed ToolLine title.
 *
 * 内部标识（run_id / conversation_id / interjection_id）不在此列，且**不要再加回来**：
 * 用户在协作图上认的是角色名，`Cancel worker r-a3f2e1c8-…` 让他无从判断 CEO 撤的是谁、
 * 处置得对不对。撤队员 / 裁决求助的标题改挂角色名（{@link RUN_TARGET_ARG_TOOLS}），
 * 查阅历史对话改挂对话标题（结果 peek）。
 * `summary` 故意不在此列：handoff 摘要走 `HandoffBriefCard`，不经 toolDetail
 * 再塞一遍（否则和卡片折叠行重复）。
 * `source` / `destination` 也不单列：file_move / file_copy 没有 `path`，须成对
 * 拼进标题，见 {@link fileTransferDetail}。 */
const TOOL_DETAIL_KEYS = [
  "query",
  "url",
  "pattern",
  "path",
  "directory",
  "command",
  "q",
  "name", // consult / consult_skill / consult_memory / consult_rule / create_folder
];

/** Full UUID — folder_id 等内部标识，不上过程行标题。 */
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function skipTitleChip(key: string, raw: string): boolean {
  const v = raw.trim();
  if (!v || v === ".") return true;
  if (key === "folder_id" || key.endsWith("_id")) return true;
  if (UUID_RE.test(v)) return true;
  return false;
}

/** 拿 `run_id` 指人的 CEO 处置工具——标题上要显示的是那名队员的角色名。 */
export const RUN_TARGET_ARG_TOOLS = new Set([
  "cancel_worker",
  "resolve_escalation",
]);

/** 形似内部标识（长十六进制串 / `wave_3` 式生成 id）——绝不进用户面标题。
 *
 * 这两个工具的 `run_id` 也接受角色名 / 短名（后端自行解析），CEO 那样填时它本就是用户
 * 认得的词，可直接显示；只有真 id 才需要靠协作图翻译，翻不出来就不显示。 */
export function looksLikeInternalId(raw: string): boolean {
  return /[0-9a-f]{8,}/i.test(raw) || /^[a-z][a-z0-9]*[_-]\d+$/i.test(raw);
}

/** Short one-liner body args (e.g. a tiny `code` snippet). Long / multiline prose
 * (`draft` / `answer` / `text` / …) stays out of the title — expand + peek cover it. */
const TOOL_DETAIL_SHORT_BODY_KEYS = ["code", "text"];

/** Collapsed title chip budget — keep the row one line (CSS truncate is the backstop). */
const TOOL_DETAIL_MAX_CHARS = 72;

function asTitleDetail(raw: string): string {
  const line = raw
    .split(/\r?\n/)
    .find((l) => l.trim())
    ?.trim();
  if (!line) return "";
  return line.length > TOOL_DETAIL_MAX_CHARS
    ? `${line.slice(0, TOOL_DETAIL_MAX_CHARS)}…`
    : line;
}

/** file_move / file_copy 标题：成对路径，避免只剩「Move file」动词。 */
function fileTransferDetail(args: Record<string, unknown>): string {
  const src = typeof args.source === "string" ? args.source.trim() : "";
  const dest =
    typeof args.destination === "string" ? args.destination.trim() : "";
  if (!src || !dest) return "";
  return asTitleDetail(`${src} → ${dest}`);
}

/** `browser` 过程行 / 组摘要的 action 细节（同构 host 的 action 芯片）。 */
export function browserToolDetail(args: Record<string, unknown>): string {
  const action = browserActionOf(args);
  if (action === "navigate") {
    return asTitleDetail(typeof args.url === "string" ? args.url : "");
  }
  if (action === "click") {
    return asTitleDetail(typeof args.ref === "string" ? args.ref : "");
  }
  if (action === "type") {
    const text =
      typeof args.text === "string" && args.text.trim() ? args.text.trim() : "";
    if (text) return asTitleDetail(text);
    return asTitleDetail(typeof args.ref === "string" ? args.ref : "");
  }
  if (action === "scroll") {
    const dy = args.dy;
    if (typeof dy === "number" && Number.isFinite(dy)) {
      return asTitleDetail(`${dy}px`);
    }
    return asTitleDetail(typeof dy === "string" ? dy : "");
  }
  return asTitleDetail(typeof args.url === "string" ? args.url : "");
}

/** `git` 过程行芯片 = subcommand，同构 host 的 action；不要搬 ApprovalPrompt 长 headline。 */
export function gitToolDetail(args: Record<string, unknown>): string {
  const sub = typeof args.subcommand === "string" ? args.subcommand.trim() : "";
  return asTitleDetail(sub);
}

/** `host` 过程行 / 组摘要的 action 细节（同构 git 的 subcommand 芯片）。 */
export function hostToolDetail(args: Record<string, unknown>): string {
  const action = hostActionOf(args);
  if (action === "shell") {
    return asTitleDetail(typeof args.command === "string" ? args.command : "");
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
    if (manager && pkg) return asTitleDetail(`${manager} ${pkg}${cask}`);
    return asTitleDetail(pkg || manager || action);
  }
  if (action === "open_settings") {
    return asTitleDetail(typeof args.panel === "string" ? args.panel : action);
  }
  if (action === "set_audio") {
    const name =
      typeof args.device_name === "string" && args.device_name.trim()
        ? args.device_name.trim()
        : typeof args.device_id === "string"
          ? args.device_id.trim()
          : "";
    return asTitleDetail(name || action);
  }
  if (action === "restart_service") {
    return asTitleDetail(
      typeof args.service === "string" ? args.service : action,
    );
  }
  if (action === "os_log") {
    return asTitleDetail(
      typeof args.source === "string" ? args.source : action,
    );
  }
  if (action === "status") {
    const facets = args.facets;
    if (Array.isArray(facets)) {
      const names = facets
        .filter(
          (f): f is string => typeof f === "string" && f.trim().length > 0,
        )
        .map((f) => f.trim());
      if (names.length > 0) return asTitleDetail(names.join(", "));
    }
    return "";
  }
  return asTitleDetail(action);
}

export function toolDetail(
  args: Record<string, unknown>,
  toolName?: string,
): string {
  if (toolName === "browser") return browserToolDetail(args);
  if (toolName === "host") return hostToolDetail(args);
  if (toolName === "git") return gitToolDetail(args);
  if (toolName === "code_execute") {
    const lang = typeof args.language === "string" ? args.language.trim() : "";
    if (lang) return asTitleDetail(lang);
  }
  if (toolName === "test_run") {
    const check = typeof args.check === "string" ? args.check.trim() : "";
    // check=command 时芯片用真实命令；枚举词 "command" 本身不进标题。
    if (check && check !== "command") return asTitleDetail(check);
  }
  const transfer = fileTransferDetail(args);
  if (transfer) return transfer;
  for (const k of TOOL_DETAIL_KEYS) {
    const v = args[k];
    if (typeof v === "string" && v.trim() && !skipTitleChip(k, v)) {
      return asTitleDetail(v);
    }
  }
  for (const k of TOOL_DETAIL_SHORT_BODY_KEYS) {
    const v = args[k];
    if (typeof v !== "string") continue;
    const trimmed = v.trim();
    if (
      !trimmed ||
      trimmed.includes("\n") ||
      trimmed.length > TOOL_DETAIL_MAX_CHARS
    ) {
      continue;
    }
    return trimmed;
  }
  // No Object.values fallback: that leaked update_synthesis `draft` (and would leak
  // other coordination prose bodies) into the title with break-all wrapping.
  return "";
}

export function baseName(detail: string): string {
  if (!detail) return "";
  const segs = detail.split(/[/\\]/);
  return segs[segs.length - 1] || detail;
}

export function toolGroupSummary(
  tools: Extract<ProcessStep, { kind: "tool" }>[],
): string {
  const sameKind = tools.every((t) => t.tool_name === tools[0].tool_name);
  // web_fetch args are bare URLs — baseName yields opaque article ids. Prefer a
  // count title (matches the merged source-collection header).
  if (sameKind && tools[0]?.tool_name === "web_fetch") {
    const n = tools.length;
    return `Read page · ${n} source${n === 1 ? "" : "s"}`;
  }
  if (sameKind && tools.length <= 3) {
    const label = groupToolLabel(tools[0]);
    const names = tools.map((t) =>
      baseName(toolDetail(t.arguments, t.tool_name)),
    );
    if (
      names.every(Boolean) &&
      tools.every(
        (t) => resolveToolWireStatus(t.status, t.failure) !== "redirect",
      )
    ) {
      return `${label} ${names.join(" · ")}`;
    }
  }
  const order: string[] = [];
  const counts = new Map<string, number>();
  for (const t of tools) {
    const label = groupToolLabel(t);
    if (!counts.has(label)) order.push(label);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return order.map((l) => `${l} ${counts.get(l)}`).join(" · ");
}

function groupToolLabel(t: Extract<ProcessStep, { kind: "tool" }>): string {
  const face = channelRedirectFace(t.failure?.code);
  if (resolveToolWireStatus(t.status, t.failure) === "redirect" && face)
    return face.label;
  return toolSummaryLabel(t.tool_name, t.arguments);
}
