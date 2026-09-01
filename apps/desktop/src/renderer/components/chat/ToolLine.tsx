import { HandoffBriefCard } from "@/components/chat/HandoffBriefCard";
import {
  debriefFromHandoffArgs,
  isSuccessfulHandoff,
} from "@/components/chat/handoffBrief";
import {
  type ToolResultData,
  ToolResultView,
  hasToolResultBody,
  toolResultPeek,
} from "@/components/chat/toolResult/ToolResultView";
import {
  codeDiagnosticsPeek,
  extractCodeDiagnostics,
} from "@/components/chat/toolResult/codeDiagnostics";
import { isVerifyBudgetExceeded } from "@/components/chat/toolResult/verifyBudget";
import { Badge, Button } from "@/components/ui";
import { isBrowserTool } from "@/lib/browserActivity";
import {
  channelRedirectFace,
  resolveToolWireStatus,
} from "@/lib/channelRedirect";
import { formatDurationSec } from "@/lib/format";
import { runningElapsedSec } from "@/lib/runningElapsed";
import { runtimeOf, useConversationStore } from "@/stores/conversation";
import {
  usePersistentDisclosure,
  useStreamAwareDisclosure,
} from "@/stores/disclosure";
import { useMessageExecution } from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import type { ProcessStep } from "@/types/events";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Radio,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  BrowserActivityCard,
  browserResultTail,
  isBrowserActivityGroup,
  isBrowserDisplay,
} from "./BrowserActivityCard";
import {
  ReadUrlSourceCollection,
  isReadUrlSourceGroup,
} from "./ReadUrlSourceCollection";
import { ThinkingDots } from "./message-bubble/Thinking";
import {
  RUN_TARGET_ARG_TOOLS,
  WRITE_FAMILY_TOOLS,
  composingWriteChars,
  looksLikeInternalId,
  toolDetail,
  toolGroupSummary,
  toolMeta,
  toolPhaseText,
} from "./message-bubble/constants";

function isToolFaultError(
  step: Extract<ProcessStep, { kind: "tool" }>,
): boolean {
  return step.status === "error" && !isVerifyBudgetExceeded(step.display);
}

/** Tools whose collapsed title already names the target (path / topic / skill / action)
 * and whose peek would only repeat an ack line or leak result body. Skip the peek —
 * collapsed rows stay a clean single line (mirrors how web_search folds its count into
 * the title instead of a peek). */
const PEEK_SUPPRESSED = new Set([
  "consult_skill",
  "consult_memory",
  "consult_rule",
  "consult",
  // 跨会话对话日志：标题已自解释（query / conversation_id），正文在展开卡。
  "search_conversations",
  // read_url / read_conversation：peek 并进标题 inlineMeta，折叠无第二行。
  "read_url",
  "read_conversation",
  // 执行类：成功 stdout 不进折叠行；失败/未完成 inlineMeta 并进标题。
  "run",
  "code_execute",
  "test_run",
  "file_read",
  "file_list",
  "glob",
  // 文件夹指挥面 + 同类漏网：折叠一行，结果只在展开。
  "list_folders",
  "resolve_folder",
  "create_folder",
  "delete_folder",
  "list_folder_dir",
  "read_folder_file",
  "remember",
  "update_folder_profile",
  "file_batch",
  "md_to_docx",
  "md_to_pdf",
  "archive_extract",
  "archive_create",
  "download_url",
  "read_image",
  "board_ops",
  "board_read",
  "code_search",
  "git",
  "code_diagnostics",
  "external_mount_readonly",
  // 派出回执不是过程信息；折叠会贴「已派出…」。
  "delegate",
  // 写盘家族：标题已有 path / source→destination；成功 ack 与路径重复。
  // 类型诊断不走第二行，折叠态并进标题（见 writeFamilyDiagnosticPeek）。
  "file_write",
  "file_append",
  "str_replace",
  "file_delete",
  "file_move",
  "file_copy",
  "mkdir",
  // CEO 协调原语：标题已自解释（撤队员 / 裁决求助另挂角色名），peek 只是操作确认文案。
  // wait 成功回执不是过程信息（无 peek / 无 chevron，见 hasToolResultBody）。
  "update_synthesis",
  "replan",
  "cancel_worker",
  "resolve_escalation",
  "queue_user_message",
  "wait",
  // grep 计数走标题 inlineMeta；未知结果形状不得再起一行贴正则/命中原文。
  "grep",
  "desktop_notify",
  // 本机 Host：标题已自解释；正文是 untrusted JSON，勿 peek 刷屏。
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

/** 模型流式组装工具调用 JSON 时的心跳行（不持久化）。写盘家族才报字数。 */
export function ComposingToolLine({
  tool,
}: {
  tool: { toolName: string; chars: number };
}) {
  const { Icon, label } = toolMeta(tool.toolName);
  const charLabel = composingWriteChars(tool.toolName, tool.chars);
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
      <Icon size={14} className="shrink-0 text-primary" />
      <span>
        {label}
        {charLabel && (
          <span className="text-muted-foreground/70">
            {" · "}
            {charLabel}
          </span>
        )}
      </span>
      <span className="inline-block animate-pulse text-primary">▋</span>
    </span>
  );
}

/** Live elapsed seconds since a tool's REAL start (`startedAt`, epoch ms stamped by
 * `addProcessTool` at `tool_use_start` — see {@link ConversationRuntime.toolStartedMs}),
 * NOT since component mount. A liveliness cue for a BLOCKING tool (web_search) whose
 * execution streams no incremental progress. Deriving from `startedAt` keeps the counter
 * stable across row remount (过程折叠/展开 · 聊天列表虚拟化), which the old mount-time anchor
 * reset to 0. The 1s ticker only forces a re-render; the value is recomputed from the wall
 * clock each render (clamped ≥0). Returns 0 when not running or the start is unknown (e.g. a
 * reloaded turn — where the tool is already done anyway, so no live timer is wanted). */
function useRunningElapsed(
  running: boolean,
  startedAt: number | null | undefined,
): number {
  const [, force] = useState(0);
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [running]);
  if (!running || startedAt == null) return 0;
  return runningElapsedSec(startedAt);
}

/**
 * 「撤回队员」/「裁决求助」的标题落谁头上——协作图上那个角色名。
 *
 * CEO 的处置动作是用户判断「这步做得对不对」的关键一行，可它的参数是 run_id。用户在图上
 * 见过的是「调研员」「审校」，见到 `r-a3f2e1c8-…` 只能放弃对账。这里按回合的协作图把目标
 * run 翻成角色名；翻不出来（历史回合无图 / 节点已不在）就什么都不显示，绝不退回摆 id。
 */
function useRunTargetRole(
  step: Extract<ProcessStep, { kind: "tool" }>,
  turnKey: string | undefined,
): string {
  const targetsRun = RUN_TARGET_ARG_TOOLS.has(step.tool_name);
  const raw =
    targetsRun && typeof step.arguments.run_id === "string"
      ? step.arguments.run_id.trim()
      : "";
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

/** Shimmer placeholder rows shown while web_search is running — turns the bare waiting
 * spinner into a「结果正在来」affordance. The search is atomic (nothing to stream), so
 * this only previews the result cards' shape until the real hits land. */
function WebSearchSkeleton() {
  return (
    <div className="mt-1 space-y-1.5" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-start gap-2 px-2 py-1">
          <div className="mt-0.5 size-4 shrink-0 animate-pulse rounded bg-muted" />
          <div className="min-w-0 flex-1 space-y-1">
            <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
            <div className="h-3 w-4/5 animate-pulse rounded bg-muted/70" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** 行尾指示（顶层工具行对齐「Read page · N sources」）：进行中用已运行秒数（取代脉冲点，
 *  折叠保持一行）；否则失败打红✗，验证未完成走 warning 三角（非故障红）；顶层可展开
 *  行补折叠 chevron。成功完成不再挂绿✓——标题本身已表明做完。 */
function ToolRowTail({
  status,
  nested,
  hasBody,
  open,
  verifyBudgetExceeded = false,
  elapsedSec = 0,
}: {
  status: "running" | "success" | "error" | "redirect";
  nested: boolean;
  hasBody: boolean;
  open: boolean;
  /** Verify budget exceeded — warning affordance, not fault red ✗. */
  verifyBudgetExceeded?: boolean;
  /** Live seconds while `status === "running"`; shown from 1s so the first tick isn't `0s`. */
  elapsedSec?: number;
}) {
  if (status === "running") {
    if (elapsedSec < 1) return null;
    return (
      <span className="ml-1.5 shrink-0 tabular-nums text-xs text-muted-foreground/70">
        {formatDurationSec(elapsedSec)}
      </span>
    );
  }
  // The verdict icon mounts fresh on the running→done edge, so a one-shot pop marks the
  // state change (设计 §3); reduced-motion skips it. 行尾只留需要动作的符号：失败红✗ /
  // 验证未完成 warning 三角 / 顶层可展开 chevron。成功完成不再挂绿✓。
  const faultIcon = verifyBudgetExceeded ? (
    <AlertTriangle
      size={14}
      className="animate-status-pop text-warning motion-reduce:animate-none"
    />
  ) : status === "error" ? (
    <X
      size={14}
      className="animate-status-pop text-destructive motion-reduce:animate-none"
    />
  ) : null;
  const chevron =
    !nested && hasBody ? (
      open ? (
        <ChevronDown size={14} className="text-muted-foreground" />
      ) : (
        <ChevronRight size={14} className="text-muted-foreground" />
      )
    ) : null;
  if (!faultIcon && !chevron) return null;
  return (
    <span className="ml-1 inline-flex items-center gap-1 align-middle">
      {faultIcon}
      {chevron}
    </span>
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
  /** 所属对话（= conversationId）：仅 browser 结果用它懒加载关键帧；其余工具忽略。 */
  conversationId?: string | null;
}) {
  const [open, setOpen] = usePersistentDisclosure(
    turnKey ? `${turnKey}:tool:${step.id}` : null,
    false,
  );
  const { Icon: ToolIcon, label: toolLabel } = toolMeta(
    step.tool_name,
    step.arguments,
  );
  const status = resolveToolWireStatus(step.status, step.failure);
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
  };
  const hasBody = hasToolResultBody(data);
  const successfulHandoff = isSuccessfulHandoff(step.tool_name, status);
  const peek = toolResultPeek(data);
  const running = status === "running";
  const verifyBudgetExceeded =
    step.status === "error" && isVerifyBudgetExceeded(step.display);
  const isWebSearch = step.tool_name === "web_search";
  // Collapsed error rows stay one line (title + red ✗ / warning 三角).
  // 验证未完成（idle/灾难顶）与其它失败态 inlineMeta 并进标题。
  const suppressesPeek =
    status === "redirect" ||
    status === "error" ||
    PEEK_SUPPRESSED.has(step.tool_name) ||
    isBrowserTool(step.tool_name) ||
    (step.tool_name === "terminal" && detail);
  // Real backend-ish start anchor (stamped at tool_use_start) keyed by tool_call_id (= step.id),
  // so the running timer survives this row remounting. Undefined on a reloaded turn (tool done).
  const startedAt = useConversationStore(
    (s) => runtimeOf(s, conversationId).toolStartedMs[step.id],
  );
  const elapsed = useRunningElapsed(running, startedAt);
  const phaseText = running ? toolPhaseText(step.phase) : null;
  // 完成态元信息并进标题行、不另起 peek：web_search「N results」、grep 匹配计数、
  // list_folders「N folders」、write 家族 / code_diagnostics 类型诊断、browser_* detail。
  const writeDiagPeek =
    status === "success" && WRITE_FAMILY_TOOLS.has(step.tool_name)
      ? writeFamilyDiagnosticPeek(data)
      : null;
  let inlineMetaWarning = false;
  const inlineMeta = (() => {
    if (status === "success") {
      if (browserTail) return browserTail;
      if (!nested && step.tool_name === "web_search" && hasBody)
        return peek || null;
      if (step.tool_name === "grep") return peek || null;
      if (step.tool_name === "list_folders") return peek || null;
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
        step.tool_name === "read_url" ||
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
  return (
    <div className="min-w-0 max-w-full">
      <Button
        variant="ghost"
        onClick={() => hasBody && setOpen((v) => !v)}
        className={`h-auto min-w-0 w-full justify-start gap-2 overflow-hidden px-0 py-0 hover:bg-transparent ${
          hasBody ? "cursor-pointer" : "cursor-default"
        }`}
      >
        <span className="flex min-w-0 w-full items-start gap-2 overflow-hidden text-left">
          <Icon size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 overflow-hidden">
            <span
              className={`flex min-w-0 items-center overflow-hidden ${
                nested
                  ? "text-sm text-foreground"
                  : "text-sm text-muted-foreground"
              }`}
            >
              <span className="min-w-0 flex-1 truncate">
                <span className={nested ? "font-medium" : undefined}>
                  {label}
                </span>
                {detail && (
                  <span className="ml-1.5 text-muted-foreground">{detail}</span>
                )}
                {phaseText && (
                  <span className="ml-1.5 text-muted-foreground/70">
                    {phaseText}
                  </span>
                )}
              </span>
              {inlineMeta && (
                <span
                  className={`ml-1.5 min-w-0 max-w-[40%] truncate ${
                    inlineMetaWarning
                      ? "text-warning/80"
                      : "text-muted-foreground/70"
                  }`}
                >
                  · {inlineMeta}
                </span>
              )}
              <ToolRowTail
                status={status}
                nested={nested}
                hasBody={hasBody}
                open={open}
                verifyBudgetExceeded={verifyBudgetExceeded}
                elapsedSec={elapsed}
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
      {running && isWebSearch && <WebSearchSkeleton />}
      {open && hasBody && <ToolResultView data={data} />}
    </div>
  );
}

/** ≥2 consecutive `web_search` — flatten to top-level ToolLines (no outer group
 * shell). Each search already carries query + result count on its own row; wrapping
 * them in「Search web A · B」only adds a redundant disclosure layer (unlike
 * read_url, which merges into one source collection). */
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
  // All-read_url groups (≥2) render as a self-folding source collection — no
  // ToolLineGroup chevron on top (would be double disclosure). Persistence key
  // stays `${turnKey}:tgrp:${groupKey}` inside ReadUrlSourceCollection.
  if (isReadUrlSourceGroup(tools)) {
    return (
      <ReadUrlSourceCollection
        tools={tools}
        isStreaming={isStreaming}
        turnKey={turnKey}
        groupKey={groupKey}
      />
    );
  }
  // All-browser_* runs (≥2) fold into one「团队浏览器」活动卡 (steps + key-frames),
  // same single-disclosure chrome as the read_url collection.
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
  // self-explanatory top-level row (query + inline result count).
  if (isWebSearchFlatGroup(tools)) {
    return (
      <div className="space-y-2">
        {tools.map((t) => (
          <ToolLine key={t.id} step={t} turnKey={turnKey} />
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
  const showBrowser = useSidePanelStore((s) => s.showBrowser);

  const summary = toolGroupSummary(tools);
  // 验证未完成不是故障，不进组头「N failed」红徽章。
  const errorCount = tools.reduce(
    (n, t) => n + (isToolFaultError(t) ? 1 : 0),
    0,
  );
  const running = tools.some((t) => t.status === "running");
  // 混杂组（含 browser_* + 他工具）走默认壳，无活动卡 CTA——组头挂同款「打开浏览器」/
  // 「查看直播」，勿在子 ToolLine 再刷。纯 browser ≥2 已由 BrowserActivityCard 接管。
  const showBrowserCta =
    conversationId != null && tools.some((t) => isBrowserTool(t.tool_name));

  return (
    <div className="min-w-0 max-w-full">
      <div className="flex min-w-0 items-center gap-1.5">
        <Button
          variant="ghost"
          onClick={toggleExpanded}
          className="h-auto min-w-0 flex-1 justify-start gap-2 overflow-hidden px-0 py-0 text-sm text-muted-foreground hover:bg-transparent hover:text-foreground"
        >
          <span className="flex min-w-0 items-center gap-2 overflow-hidden">
            {running && <ThinkingDots />}
            <span className="min-w-0 truncate text-left">{summary}</span>
            {errorCount > 0 && (
              <Badge tone="destructive" className="shrink-0 font-normal">
                {errorCount} failed
              </Badge>
            )}
            {!running &&
              (expanded ? (
                <ChevronDown size={14} className="shrink-0" />
              ) : (
                <ChevronRight size={14} className="shrink-0" />
              ))}
          </span>
        </Button>
        {showBrowserCta && (
          <button
            type="button"
            onClick={showBrowser}
            className="flex shrink-0 items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/15"
          >
            <Radio size={12} className="shrink-0" />
            {running ? "查看直播" : "打开浏览器"}
          </button>
        )}
      </div>
      {expanded && (
        <div className="mt-1.5 space-y-2 pl-3">
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
