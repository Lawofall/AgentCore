import { HandoffBriefCard } from "@/components/chat/HandoffBriefCard";
import {
  debriefFromHandoffArgs,
  isSuccessfulHandoff,
} from "@/components/chat/handoffBrief";
import {
  type ToolLineTitleStat,
  type ToolResultData,
  ToolResultView,
  hasToolResultBody,
  toolLineTitleStat,
  toolResultPeek,
} from "@/components/chat/toolResult/ToolResultView";
import {
  codeDiagnosticsPeek,
  extractCodeDiagnostics,
} from "@/components/chat/toolResult/codeDiagnostics";
import {
  toolGroupFaultLabel,
  toolRowFaultLabel,
} from "@/components/chat/toolResult/toolFaultFace";
import { isVerifyBudgetExceeded } from "@/components/chat/toolResult/verifyBudget";
import { Button } from "@/components/ui";
import { isBrowserTool } from "@/lib/browserActivity";
import {
  channelRedirectFace,
  resolveToolWireStatus,
} from "@/lib/channelRedirect";
import { notifyActionError } from "@/lib/toast";
import { openCloudPreview } from "@/services/openCloudPreview";
import { useStreamAwareDisclosure } from "@/stores/disclosure";
import { useToolOutputLiveStore } from "@/stores/toolOutputLive";
import { useMessageExecution } from "@/stores/execution";
import type { ProcessStep } from "@/types/events";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import {
  BrowserActivityCard,
  browserResultTail,
  isBrowserActivityGroup,
  isBrowserDisplay,
} from "./BrowserActivityCard";
import {
  WebFetchSourceCollection,
  isWebFetchSourceGroup,
} from "./WebFetchSourceCollection";
import {
  LiveFlow,
  LiveFlowDots,
  LiveFlowText,
} from "./message-bubble/LiveFlow";
import {
  RUN_TARGET_ARG_TOOLS,
  WRITE_FAMILY_TOOLS,
  composingWriteChars,
  looksLikeInternalId,
  toolDetail,
  toolGroupSummary,
  toolMeta,
  toolRowPhaseLabel,
} from "./message-bubble/constants";

/** Tools whose collapsed title already names the target (path / topic / skill / action)
 * and whose peek would only repeat an ack line or leak result body. Skip the peek —
 * collapsed rows stay a clean single line. */
const PEEK_SUPPRESSED = new Set([
  "consult_skill",
  "consult_memory",
  "consult_rule",
  "consult",
  // 跨会话对话日志：标题已自解释；read 对话标题走 inlineMeta。
  "search_conversations",
  // web_search：折叠不挂条数。web_fetch / read_conversation：标题并进 inlineMeta。
  "web_search",
  "web_fetch",
  "read_conversation",
  // 执行类：成功 stdout 不进折叠行；失败「未通过」并进标题。
  // terminal 与 run 同：命令在展开里，不靠标题芯片来压住输出预览。
  "run",
  "code_execute",
  "test_run",
  "terminal",
  "read",
  "file_list",
  "glob",
  // 文件夹指挥面 + 同类漏网：折叠一行，结果只在展开。
  "list_folders",
  "resolve_folder",
  "folders",
  "create_folder",
  "delete_folder",
  "remember",
  "update_folder_profile",
  "file_batch",
  "md_to_docx",
  "md_to_pdf",
  "md_export",
  "read_image",
  "code_search",
  "git",
  "code_diagnostics",
  "external_mount_readonly",
  // 派出回执不是过程信息；折叠会贴「已派出…」。
  "delegate",
  // 写盘家族：标题已有 file_path / source→destination；成功 ack 与路径重复。
  // 类型诊断不走第二行，折叠态并进标题（见 writeFamilyDiagnosticPeek）。
  "write",
  "edit",
  "file_delete",
  "file_move",
  "file_copy",
  "file_batch",
  "mkdir",
  // CEO 协调原语：标题已自解释（撤队员 / 裁决求助另挂角色名），peek 只是操作确认文案。
  "update_synthesis",
  "replan",
  "cancel_worker",
  "queue_user_message",
  // grep：标题已有 pattern；命中列表只在展开。折叠不挂计数 / 未匹配。
  "grep",
  // 本机 Host：标题已自解释；折叠不 peek。
  "host",
  // 单工具 browser 的 peek 由 isBrowserTool 覆盖（精确名 + 历史 browser_*）。
  // 历史会话：旧 host_* 仍抑制 peek。
  "host_ping",
  "host_info",
  "host_audio_devices",
  "host_storage",
  "host_power",
  "host_network_summary",
  "host_apps",
  "host_os_log_summary",
  "host_shell",
  "host_open_settings",
  "host_audio_set_default",
  "host_service_restart",
  "host_package_install",
]);

/** Write-family collapsed row: only surface diagnostics (errors / unavailable).
 * Clean「未发现类型错误」falls through to the path title — same as a suppressed ack. */
function writeFamilyDiagnosticPeek(data: ToolResultData): string | null {
  const diag = extractCodeDiagnostics(data.display);
  if (!diag) return null;
  const text = codeDiagnosticsPeek(diag);
  if (diag.status === "unavailable" || text !== "未发现类型错误") return text;
  return null;
}

/** 模型流式组装工具调用 JSON 时的心跳行（不持久化）。写盘与 delegate/debate 报字数。 */
export function ComposingToolLine({
  tool,
}: {
  tool: { toolName: string; chars: number };
}) {
  const { Icon, label } = toolMeta(tool.toolName);
  const charLabel = composingWriteChars(tool.toolName, tool.chars);
  return (
    <LiveFlow
      active
      className="inline-flex items-center gap-2 text-sm text-muted-foreground"
    >
      <LiveFlowDots active />
      <Icon size={14} className="shrink-0 text-primary" />
      <LiveFlowText>{label}</LiveFlowText>
      {charLabel && (
        <span className="text-muted-foreground/70">
          {" · "}
          {charLabel}
        </span>
      )}
    </LiveFlow>
  );
}

/**
 * 「撤回队员」/「裁决求助」的标题落谁头上——协作图上那个角色名。
 *
 * CEO 的处置动作是用户判断「这步做得对不对」的关键一行，可它的参数是 run_id。用户在图上
 * 见过的是「调研员」「审校」，见到 `r-a3f2e1c8-…` 只能放弃对账。这里按回合的协作图把目标
 * run 翻成角色名；翻不出来（历史回合无图 / 节点已不在）就什么都不显示，绝不退回摆 id。
 */
function runTargetArgument(
  step: Extract<ProcessStep, { kind: "tool" }>,
): string {
  if (!RUN_TARGET_ARG_TOOLS.has(step.tool_name)) return "";
  if (typeof step.arguments.run_id === "string") {
    return step.arguments.run_id.trim();
  }
  const tell = step.arguments.tell;
  if (!Array.isArray(tell) || tell.length !== 1) return "";
  const first = tell[0];
  if (
    first &&
    typeof first === "object" &&
    typeof (first as { run_id?: unknown }).run_id === "string"
  ) {
    return (first as { run_id: string }).run_id.trim();
  }
  return "";
}

function useRunTargetRole(
  step: Extract<ProcessStep, { kind: "tool" }>,
  turnKey: string | undefined,
): string {
  const raw = runTargetArgument(step);
  const execution = useMessageExecution(raw ? (turnKey ?? null) : null);
  if (!raw) return "";
  const run = execution?.runs.find((r) => r.id === raw);
  if (run) {
    const role =
      execution?.agents.find((a) => a.id === run.agentId)?.role ?? run.role;
    if (role?.trim()) return role.trim();
  }
  return looksLikeInternalId(raw) ? "" : raw;
}

/** edit +/- (omit zeros), write「N 行」, or a read window
 * (`42–53 行`) — shrink-0 so the path truncates first. Diagnostics stay in
 * inlineMeta (warning) after this. */
function ToolLineStat({ stat }: { stat: ToolLineTitleStat }) {
  if (stat.kind === "diff") {
    return (
      <span className="ml-1.5 flex shrink-0 items-center gap-1.5 tabular-nums">
        {stat.adds > 0 && <span className="text-success">+{stat.adds}</span>}
        {stat.dels > 0 && (
          <span className="text-destructive">-{stat.dels}</span>
        )}
      </span>
    );
  }
  if (stat.kind === "readWindow") {
    return (
      <span className="ml-1.5 shrink-0 tabular-nums text-muted-foreground/70">
        {stat.start}–{stat.end} 行
      </span>
    );
  }
  return (
    <span className="ml-1.5 shrink-0 tabular-nums text-muted-foreground/70">
      {stat.lines} 行
    </span>
  );
}

/** 行尾指示：进行中不跟秒（流光即心跳），但已有正文仍留 chevron；验证没过挂灰色「未通过」；
 *  验证未完成走 warning 三角；顶层可展开行补 chevron。成功不挂标记。查找失败 / 默认失败不挂同义词。 */
function ToolRowTail({
  status,
  nested,
  hasBody,
  open,
  verifyBudgetExceeded = false,
  faultLabel = null,
}: {
  status: "running" | "success" | "error" | "redirect";
  nested: boolean;
  hasBody: boolean;
  open: boolean;
  /** Verify budget exceeded — warning affordance, not a fault word. */
  verifyBudgetExceeded?: boolean;
  /** 未通过 — uncolored. Lookup / generic faults hang nothing. */
  faultLabel?: string | null;
}) {
  const chevron =
    !nested && hasBody ? (
      open ? (
        <ChevronDown size={14} className="text-muted-foreground" />
      ) : (
        <ChevronRight size={14} className="text-muted-foreground" />
      )
    ) : null;
  if (status === "running") {
    if (!chevron) return null;
    return (
      <span className="ml-1 inline-flex items-center gap-1 align-middle">
        {chevron}
      </span>
    );
  }
  const faultMeta =
    !verifyBudgetExceeded && faultLabel ? (
      <span
        data-testid="tool-fault-label"
        className="text-xs text-muted-foreground/70"
      >
        {faultLabel}
      </span>
    ) : null;
  const warningIcon = verifyBudgetExceeded ? (
    <AlertTriangle
      size={14}
      className="animate-status-pop text-warning motion-reduce:animate-none"
    />
  ) : null;
  if (!faultMeta && !warningIcon && !chevron) return null;
  return (
    <span className="ml-1 inline-flex items-center gap-1 align-middle">
      {faultMeta}
      {warningIcon}
      {chevron}
    </span>
  );
}

/** `run` display 上的云端预览入口（后端 `preview_available` + `process_id`）。 */
function runCloudPreview(
  step: Extract<ProcessStep, { kind: "tool" }>,
  conversationId: string | null | undefined,
): { conversationId: string; processId: string; ports: number[] } | null {
  if (step.tool_name !== "run" || !conversationId) return null;
  const display = step.display;
  if (!display || typeof display !== "object") return null;
  const rec = display as Record<string, unknown>;
  if (rec.preview_available !== true) return null;
  const processId =
    typeof rec.process_id === "string" ? rec.process_id.trim() : "";
  if (!processId) return null;
  const raw = rec.http_ports;
  const ports: number[] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      const n = typeof item === "number" ? item : Number(item);
      if (Number.isInteger(n) && n > 0 && n <= 65535) ports.push(n);
    }
  }
  return { conversationId, processId, ports };
}

/** 云端 run 预览 chip：换票后在系统浏览器打开。 */
function CloudPreviewButtons({
  conversationId,
  processId,
  ports,
}: {
  conversationId: string;
  processId: string;
  ports: number[];
}) {
  const items =
    ports.length > 1
      ? ports.map((port) => ({ port, label: `打开预览 · ${port}` }))
      : [{ port: undefined, label: "打开预览" }];
  return (
    <>
      {items.map(({ port, label }) => (
        <button
          key={port ?? "default"}
          type="button"
          onClick={() => {
            void openCloudPreview({
              conversationId,
              processId,
              port,
            }).catch((e) => notifyActionError("打开预览失败", e));
          }}
          className="flex shrink-0 items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/15"
        >
          <ExternalLink size={12} className="shrink-0" />
          {label}
        </button>
      ))}
    </>
  );
}

function readConversationOpenId(
  step: Extract<ProcessStep, { kind: "tool" }>,
): string | null {
  if (step.tool_name !== "read_conversation") return null;
  const d = step.display;
  if (!d || typeof d !== "object") return null;
  const id = (d as { conversation_id?: unknown }).conversation_id;
  return typeof id === "string" && id.trim() ? id.trim() : null;
}

/** Sibling of the expand title — does not toggle the transcript. */
function OpenConversationButton({
  conversationId,
}: { conversationId: string }) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      onClick={() => navigate(`/conversations/${conversationId}`)}
      className="shrink-0 text-xs text-muted-foreground hover:text-foreground hover:underline"
    >
      打开
    </button>
  );
}

/** Single tool invocation row in the process timeline. */
export function ToolLine({
  step,
  turnKey,
  nested = false,
  conversationId = null,
}: {
  step: Extract<ProcessStep, { kind: "tool" }>;
  /** 回合作用域标识（= messageId）：给了才把「结果卡开合」持久化（切对话/刷新后仍在），
   *  按 `${turnKey}:tool:${step.id}` 落 localStorage；缺省（如渲染测试）退化为会话内存态。 */
  turnKey?: string;
  /** 是否为「工具组展开后的缩进明细子行」。顶层孤立工具行（默认 false）走 header 规格
   *  （text-sm·灰·不加粗），与思考过程/工具组/过程摘要同级；组内子行（true）保留
   *  明细规格（text-sm·深色·加粗），靠 pl-3 缩进与父摘要行区分层级。 */
  nested?: boolean;
  /** 所属对话（= conversationId）：browser 关键帧懒加载；云端 run「打开预览」换票。 */
  conversationId?: string | null;
}) {
  const status = resolveToolWireStatus(step.status, step.failure);
  const running = status === "running";
  const commandArg =
    typeof step.arguments.command === "string"
      ? step.arguments.command.trim()
      : "";
  const liveTerminal =
    running &&
    commandArg.length > 0 &&
    (step.tool_name === "run" ||
      step.tool_name === "terminal" ||
      step.tool_name === "host" ||
      step.tool_name === "host_shell" ||
      step.tool_name === "test_run");
  const [open, toggleOpen] = useStreamAwareDisclosure(
    turnKey ? `${turnKey}:tool:${step.id}` : null,
    liveTerminal,
  );
  const liveOut = useToolOutputLiveStore((s) =>
    open ? s.byId[step.id] : undefined,
  );
  const { Icon: ToolIcon, label: toolLabel } = toolMeta(
    step.tool_name,
    step.arguments,
  );
  const redirectFace =
    status === "redirect" ? channelRedirectFace(step.failure?.code) : null;
  const Icon = redirectFace
    ? toolMeta(redirectFace.toolName, {}).Icon
    : ToolIcon;
  const label = redirectFace?.label ?? toolLabel;
  const targetRole = useRunTargetRole(step, turnKey);
  const browserTail =
    status === "success" && isBrowserDisplay(step.display)
      ? browserResultTail(step.display) || null
      : null;
  // display.detail 已进标题时不再 chip args.url / text，避免 Navigate 叠两遍 URL。
  // Redirect rows name the destination verb; do not peek the rejected call's args.
  const detail = redirectFace
    ? ""
    : browserTail
      ? ""
      : targetRole || toolDetail(step.arguments, step.tool_name);
  const data: ToolResultData = {
    toolName: step.tool_name,
    args: step.arguments,
    result: step.result,
    display: step.display,
    failure: step.failure,
    status,
    conversationId,
    liveStdout: running ? liveOut?.stdout : undefined,
    liveStderr: running ? liveOut?.stderr : undefined,
  };
  const hasBody = hasToolResultBody(data);
  const successfulHandoff = isSuccessfulHandoff(step.tool_name, status);
  const peek = toolResultPeek(data);
  const verifyBudgetExceeded =
    step.status === "error" && isVerifyBudgetExceeded(step.display);
  const faultLabel = toolRowFaultLabel(step);
  // Collapsed error rows stay one line (title + 未通过 / warning 三角).
  // 验证未完成（idle/灾难顶）inlineMeta 并进标题；查找失败 / 默认失败不挂同义词。
  const suppressesPeek =
    status === "redirect" ||
    status === "error" ||
    PEEK_SUPPRESSED.has(step.tool_name) ||
    isBrowserTool(step.tool_name);
  const phaseText = running ? toolRowPhaseLabel(step.phase) : null;
  // 完成态元信息并进标题行、不另起 peek：edit +/-、write「N 行」、
  // read 窗口「a–b 行」、write 家族 / code_diagnostics、browser 页标题或 URL、
  // web_fetch / read_conversation 标题。检索 / 盘点条数不进折叠行。
  const titleStat = toolLineTitleStat(data);
  const writeDiagPeek =
    status === "success" && WRITE_FAMILY_TOOLS.has(step.tool_name)
      ? writeFamilyDiagnosticPeek(data)
      : null;
  let inlineMetaWarning = false;
  const inlineMeta = (() => {
    if (status === "success") {
      if (browserTail) return browserTail;
      if (step.tool_name === "code_diagnostics") {
        const diag = extractCodeDiagnostics(data.display);
        if (diag) {
          const text = codeDiagnosticsPeek(diag);
          if (diag.status === "unavailable" || text !== "未发现类型错误") {
            inlineMetaWarning = true;
          }
        }
        return peek || null;
      }
      if (writeDiagPeek) {
        inlineMetaWarning = true;
        return writeDiagPeek;
      }
      if (
        step.tool_name === "web_fetch" ||
        step.tool_name === "read_conversation"
      ) {
        return peek || null;
      }
      return null;
    }
    if (status === "error") {
      if (verifyBudgetExceeded) {
        inlineMetaWarning = true;
        return peek || null;
      }
      const execTool =
        step.tool_name === "run" ||
        step.tool_name === "code_execute" ||
        step.tool_name === "test_run" ||
        step.tool_name === "terminal";
      if (execTool && peek) return peek;
    }
    return null;
  })();
  if (successfulHandoff) {
    return (
      <HandoffBriefCard
        debrief={debriefFromHandoffArgs(step.arguments)}
        persistKey={turnKey ? `${turnKey}:tool:${step.id}` : null}
      />
    );
  }
  const preview = runCloudPreview(step, conversationId);
  const openConversationId = readConversationOpenId(step);
  const titleBtn = (
    <Button
      variant="ghost"
      onClick={() => hasBody && toggleOpen()}
      className={`h-auto min-w-0 w-full justify-start gap-2 overflow-hidden px-0 py-0 font-normal hover:bg-transparent ${
        hasBody ? "cursor-pointer" : "cursor-default"
      }`}
    >
      <span className="flex min-w-0 w-full items-start gap-2 overflow-hidden text-left">
        <span className="flex h-5 shrink-0 items-center justify-center text-muted-foreground">
          <Icon size={14} />
        </span>
        {running && <LiveFlowDots active />}
        <span className="min-w-0 flex-1 overflow-hidden">
          <span
            className={`flex h-5 min-w-0 items-center overflow-hidden ${
              nested
                ? "text-sm text-foreground"
                : "text-sm text-muted-foreground"
            }`}
          >
            <span className="min-w-0 flex-1 truncate">
              <LiveFlowText className={nested ? "font-medium" : undefined}>
                {label}
              </LiveFlowText>
              {detail && (
                <span className="ml-1.5 text-muted-foreground">
                  <LiveFlowText>{detail}</LiveFlowText>
                </span>
              )}
              {phaseText && (
                <span className="ml-1.5 text-muted-foreground/70">
                  {phaseText}
                </span>
              )}
            </span>
            {titleStat && <ToolLineStat stat={titleStat} />}
            {inlineMeta && (
              <span
                className={`ml-1.5 min-w-0 max-w-[40%] truncate ${
                  inlineMetaWarning
                    ? "text-warning/80"
                    : "text-muted-foreground/70"
                }`}
              >
                {inlineMeta}
              </span>
            )}
            <ToolRowTail
              status={status}
              nested={nested}
              hasBody={hasBody}
              open={open}
              verifyBudgetExceeded={verifyBudgetExceeded}
              faultLabel={faultLabel}
            />
          </span>
          {hasBody && !open && !inlineMeta && !suppressesPeek && (
            <span className="block truncate text-xs text-muted-foreground/70">
              {peek}
            </span>
          )}
        </span>
      </span>
    </Button>
  );
  return (
    <div className="min-w-0 max-w-full">
      {preview || openConversationId ? (
        <div className="flex min-w-0 items-center gap-1.5">
          <LiveFlow active={running} className="min-w-0 flex-1 overflow-hidden">
            {titleBtn}
          </LiveFlow>
          {preview ? (
            <CloudPreviewButtons
              conversationId={preview.conversationId}
              processId={preview.processId}
              ports={preview.ports}
            />
          ) : null}
          {openConversationId ? (
            <OpenConversationButton conversationId={openConversationId} />
          ) : null}
        </div>
      ) : (
        <LiveFlow active={running} className="min-w-0 w-full">
          {titleBtn}
        </LiveFlow>
      )}
      {open && hasBody && <ToolResultView data={data} />}
    </div>
  );
}

/** ≥2 consecutive `web_search` — flatten to top-level ToolLines (no outer group
 * shell). Each search already carries its query on its own row; wrapping them in
 * 「Search web A · B」only adds a redundant disclosure layer (unlike web_fetch,
 * which merges into one source collection). */
function isWebSearchFlatGroup(
  tools: Extract<ProcessStep, { kind: "tool" }>[],
): boolean {
  return tools.length >= 2 && tools.every((t) => t.tool_name === "web_search");
}

/** Collapsible group of consecutive tool lines (ProcessToolGroup pattern). */
export function ToolLineGroup({
  tools,
  isStreaming,
  turnKey,
  groupKey,
  conversationId = null,
}: {
  tools: Extract<ProcessStep, { kind: "tool" }>[];
  isStreaming: boolean;
  /** 回合作用域标识（= messageId）：给了才把「工具组开合」持久化；缺省退化为会话内存态。 */
  turnKey?: string;
  /** 该工具组的稳定标识（timelineNodeKeys，首个 tool 的 id）——组成持久化键；
   *  标记中段插入（insertBeforeTeam）不再位移它。 */
  groupKey?: string;
  /** 所属对话（= conversationId）：透传给 browser 活动卡懒加载关键帧；其余分派忽略。 */
  conversationId?: string | null;
}) {
  // All-web_fetch groups (≥2) render as a self-folding source collection — no
  // ToolLineGroup chevron on top (would be double disclosure). Persistence key
  // stays `${turnKey}:tgrp:${groupKey}` inside WebFetchSourceCollection.
  if (isWebFetchSourceGroup(tools)) {
    return (
      <WebFetchSourceCollection
        tools={tools}
        isStreaming={isStreaming}
        turnKey={turnKey}
        groupKey={groupKey}
      />
    );
  }
  // All-browser_* runs (≥2) fold into one「团队浏览器」活动卡 (steps + key-frames),
  // same single-disclosure chrome as the web_fetch collection.
  if (isBrowserActivityGroup(tools)) {
    return (
      <BrowserActivityCard
        tools={tools}
        isStreaming={isStreaming}
        turnKey={turnKey}
        groupKey={groupKey}
        conversationId={conversationId}
      />
    );
  }
  // Pure web_search runs: skip the outer group shell — each call is already a
  // self-explanatory top-level row (query on the title).
  if (isWebSearchFlatGroup(tools)) {
    return (
      <div className="space-y-2">
        {tools.map((t) => (
          <ToolLine
            key={t.id}
            step={t}
            turnKey={turnKey}
            conversationId={conversationId}
          />
        ))}
      </div>
    );
  }
  return (
    <DefaultToolLineGroup
      tools={tools}
      isStreaming={isStreaming}
      turnKey={turnKey}
      groupKey={groupKey}
      conversationId={conversationId}
    />
  );
}

function DefaultToolLineGroup({
  tools,
  isStreaming,
  turnKey,
  groupKey,
  conversationId = null,
}: {
  tools: Extract<ProcessStep, { kind: "tool" }>[];
  isStreaming: boolean;
  turnKey?: string;
  groupKey?: string;
  conversationId?: string | null;
}) {
  // 「直播中自动展开盯着看、收场后按保存值」（Q3）：取代旧的「流式默认展开 + 收场强制收起」，
  // 收场后不再强收，而是回到用户持久化的选择。
  const [expanded, toggleExpanded] = useStreamAwareDisclosure(
    turnKey != null && groupKey != null ? `${turnKey}:tgrp:${groupKey}` : null,
    isStreaming,
  );

  const summary = toolGroupSummary(tools);
  const groupFault = !expanded ? toolGroupFaultLabel(tools) : null;
  const running = tools.some((t) => t.status === "running");
  const headerLive = running && !expanded;
  return (
    <div className="min-w-0 max-w-full">
      <LiveFlow active={headerLive} className="min-w-0 w-full">
        <Button
          variant="ghost"
          onClick={toggleExpanded}
          className="h-auto min-w-0 w-full justify-start gap-2 overflow-hidden px-0 py-0 text-sm font-normal text-muted-foreground hover:bg-transparent hover:text-foreground"
        >
          <span className="flex min-w-0 items-center gap-2 overflow-hidden">
            {headerLive && <LiveFlowDots active />}
            <LiveFlowText className="min-w-0 truncate text-left">
              {summary}
            </LiveFlowText>
            {groupFault && (
              <span
                data-testid="tool-group-fault"
                className="shrink-0 text-xs text-muted-foreground/70"
              >
                {groupFault}
              </span>
            )}
            {expanded ? (
              <ChevronDown size={14} className="shrink-0" />
            ) : (
              <ChevronRight size={14} className="shrink-0" />
            )}
          </span>
        </Button>
      </LiveFlow>
      {expanded && (
        <div className="mt-2 space-y-2 pl-3">
          {tools.map((t) => (
            <ToolLine
              key={t.id}
              step={t}
              turnKey={turnKey}
              nested
              conversationId={conversationId}
            />
          ))}
        </div>
      )}
    </div>
  );
}
