import { isSuccessfulHandoff } from "@/components/chat/handoffBrief";
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
import { isFileReadCeilingGuidance } from "@/components/chat/toolResult/fileReadCeiling";
import { isVerifyBudgetExceeded } from "@/components/chat/toolResult/verifyBudget";
import { Badge, Button } from "@/components/ui";
import { isBrowserTool } from "@/lib/browserActivity";
import { formatCompact } from "@/lib/format";
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
  Check,
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
  looksLikeInternalId,
  toolDetail,
  toolGroupSummary,
  toolMeta,
  toolPhaseText,
} from "./message-bubble/constants";

function isToolFaultError(
  step: Extract<ProcessStep, { kind: "tool" }>,
): boolean {
  return (
    step.status === "error" &&
    !isFileReadCeilingGuidance(step.tool_name, step.result) &&
    !isVerifyBudgetExceeded(step.display)
  );
}

function isToolCeilingGuidance(
  step: Extract<ProcessStep, { kind: "tool" }>,
): boolean {
  return (
    step.status === "error" &&
    (isFileReadCeilingGuidance(step.tool_name, step.result) ||
      isVerifyBudgetExceeded(step.display))
  );
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
  // read_conversation 不在此列：标题不再拼 conversation_id，改由 peek 亮出对话标题——
  // 用户认的是「上次那场讨论」，不是一串 id。
  "file_read",
  "file_list",
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
  "post_note",
  "amend_note",
  "desktop_notify",
  // 本机 Host：标题已自解释；正文是 untrusted JSON，勿 peek 刷屏。
  "host",
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

const WRITE_FAMILY = new Set(["file_write", "file_append", "str_replace"]);

/** Write-family collapsed row: only surface diagnostics (errors / unavailable).
 * Clean「未发现类型错误」falls through to the path title — same as a suppressed ack. */
function writeFamilyDiagnosticPeek(data: ToolResultData): string | null {
  const diag = extractCodeDiagnostics(data.display);
  if (!diag) return null;
  const text = codeDiagnosticsPeek(diag);
  if (diag.status === "unavailable" || text !== "未发现类型错误") return text;
  return null;
}

/** 模型流式组装工具调用 JSON 时的心跳行（不持久化）。 */
export function ComposingToolLine({
  tool,
}: {
  tool: { toolName: string; chars: number };
}) {
  const { Icon, label } = toolMeta(tool.toolName);
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
      <Icon size={14} className="shrink-0 text-primary" />
      <span>
        正在组装 {label}
        {tool.chars > 0 && (
          <span className="text-muted-foreground/70">
            {" · "}
            {formatCompact(tool.chars)} 字
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

/** 行尾指示（顶层工具行对齐「Read page · N sources」）：进行中脉冲点；否则失败打红✗，
 *  file_read 同 path 天花板走 warning 三角（引导态，非故障红）；顶层可展开行补折叠
 *  chevron；组内明细子行仍用成功绿✓。顶层成功即只留 chevron（与 read_url 组一致）。 */
function ToolRowTail({
  status,
  nested,
  hasBody,
  open,
  ceilingGuidance = false,
}: {
  status: "running" | "success" | "error";
  nested: boolean;
  hasBody: boolean;
  open: boolean;
  /** Same-path file_read ceiling — warning affordance, not fault red ✗. */
  ceilingGuidance?: boolean;
}) {
  if (status === "running")
    return (
      <span className="ml-1.5 inline-block size-1.5 animate-pulse rounded-full bg-primary align-middle" />
    );
  // The verdict icon mounts fresh on the running→done edge, so a one-shot pop marks the
  // state change (设计 §3); reduced-motion skips it. 行尾指示紧跟标题文字（自适应内容右侧、
  // 不撑到行边缘）：失败红✗ / 天花板 warning 三角；顶层可展开补折叠 chevron；组内明细用绿✓。
  return (
    <span className="ml-1 inline-flex items-center gap-1 align-middle">
      {status === "error" &&
        (ceilingGuidance ? (
          <AlertTriangle
            size={14}
            className="animate-status-pop text-warning motion-reduce:animate-none"
          />
        ) : (
          <X
            size={14}
            className="animate-status-pop text-destructive motion-reduce:animate-none"
          />
        ))}
      {nested && status === "success" && (
        <Check
          size={14}
          className="animate-status-pop text-success motion-reduce:animate-none"
        />
      )}
      {!nested &&
        hasBody &&
        (open ? (
          <ChevronDown size={14} className="text-muted-foreground" />
        ) : (
          <ChevronRight size={14} className="text-muted-foreground" />
        ))}
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
   *  （text-sm·灰·不加粗·成功无✓），与思考过程/工具组/过程摘要同级；组内子行（true）保留
   *  明细规格（text-sm·深色·加粗·成功绿✓），靠 pl-3 缩进与父摘要行区分层级。 */
  nested?: boolean;
  /** 所属对话（= conversationId）：仅 browser 结果用它懒加载关键帧；其余工具忽略。 */
  conversationId?: string | null;
}) {
  const [open, setOpen] = usePersistentDisclosure(
    turnKey ? `${turnKey}:tool:${step.id}` : null,
    false,
  );
  const { Icon, label } = toolMeta(step.tool_name, step.arguments);
  const targetRole = useRunTargetRole(step, turnKey);
  const browserTail =
    step.status === "success" && isBrowserDisplay(step.display)
      ? browserResultTail(step.display) || null
      : null;
  // display.detail 已进标题时不再 chip args.url / text，避免 Navigate 叠两遍 URL。
  const detail = browserTail
    ? ""
    : targetRole || toolDetail(step.arguments, step.tool_name);
  const data: ToolResultData = {
    toolName: step.tool_name,
    args: step.arguments,
    result: step.result,
    display: step.display,
    failure: step.failure,
    status: step.status,
    conversationId,
  };
  const hasBody = hasToolResultBody(data);
  const successfulHandoff = isSuccessfulHandoff(step.tool_name, step.status);
  const peek = toolResultPeek(data);
  const running = step.status === "running";
  const ceilingGuidance = isToolCeilingGuidance(step);
  const isWebSearch = step.tool_name === "web_search";
  // Product failure face must stay visible on the collapsed row even for tools
  // whose success peek is suppressed (title already self-explanatory).
  const suppressesPeek =
    (PEEK_SUPPRESSED.has(step.tool_name) || isBrowserTool(step.tool_name)) &&
    !(step.status === "error" && !!step.failure?.message?.trim());
  // Real backend-ish start anchor (stamped at tool_use_start) keyed by tool_call_id (= step.id),
  // so the running timer survives this row remounting. Undefined on a reloaded turn (tool done).
  const startedAt = useConversationStore(
    (s) => runtimeOf(s, conversationId).toolStartedMs[step.id],
  );
  const elapsed = useRunningElapsed(running, startedAt);

  // Waiting-state hint (network search UX): coarse phase (Searching / Queued / Trying fallback)
  // plus a live elapsed timer, replacing the dead spinner. Empty at the very first instant
  // (no phase yet, <1s) — the pulsing dot + skeleton still convey life.
  const runningHint = running
    ? [toolPhaseText(step.phase), elapsed >= 1 ? `${elapsed}s` : null]
        .filter(Boolean)
        .join(" · ")
    : "";
  // 完成态元信息并进标题行、不另起 peek：web_search「N results」、grep 匹配计数、
  // write 家族类型诊断、browser_* detail。handoff summary 走 inlineBody。
  const writeDiagPeek =
    step.status === "success" && WRITE_FAMILY.has(step.tool_name)
      ? writeFamilyDiagnosticPeek(data)
      : null;
  const inlineMeta =
    step.status !== "success"
      ? null
      : browserTail
        ? browserTail
        : !nested && step.tool_name === "web_search" && hasBody
          ? peek || null
          : step.tool_name === "grep"
            ? peek || null
            : writeDiagPeek;
  const inlineBody = successfulHandoff && peek ? peek : null;
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
                {inlineBody && (
                  <span className="ml-1.5 text-muted-foreground">
                    {inlineBody}
                  </span>
                )}
              </span>
              {inlineMeta && (
                <span
                  className={`ml-1.5 min-w-0 max-w-[40%] truncate ${
                    writeDiagPeek
                      ? "text-warning/80"
                      : "text-muted-foreground/70"
                  }`}
                >
                  · {inlineMeta}
                </span>
              )}
              <ToolRowTail
                status={step.status}
                nested={nested}
                hasBody={hasBody}
                open={open}
                ceilingGuidance={ceilingGuidance}
              />
            </span>
            {runningHint && (
              <span className="block truncate text-xs text-muted-foreground/70">
                {runningHint}
              </span>
            )}
            {(hasBody || (successfulHandoff && !!peek)) &&
              !open &&
              !inlineMeta &&
              !inlineBody &&
              !suppressesPeek && (
                <span
                  className={`block truncate text-xs ${
                    ceilingGuidance
                      ? "text-warning/80"
                      : step.status === "error"
                        ? "text-destructive/80"
                        : "text-muted-foreground/70"
                  }`}
                >
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
  // file_read 天花板是引导态，不进组头「N failed」红徽章。
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
