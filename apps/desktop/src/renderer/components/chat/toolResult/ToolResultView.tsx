import {
  BrowserResult,
  browserResultPeek,
  isBrowserDisplay,
} from "@/components/chat/BrowserActivityCard";
import { DebriefDetails } from "@/components/chat/detail/sections/RunDebrief";
import {
  debriefFromHandoffArgs,
  handoffSummaryPeek,
  hasDebriefDetails,
  isSuccessfulHandoff,
} from "@/components/chat/handoffBrief";
import { PromptDocument } from "@/components/prompt/PromptDocument";
import { cleanSourceTitle } from "@/lib/citations";
import type {
  CodeExecDisplay,
  ConversationLogDisplay,
  MemoryConsultDisplay,
  ReadUrlDisplay,
  RuleConsultDisplay,
  SkillConsultDisplay,
  ToolDisplay,
  ToolFailure,
  UnifiedConsultDisplay,
  WebSearchDisplay,
} from "@/types/events";
import {
  BookOpen,
  Brain,
  FileCode2,
  FileText,
  MessagesSquare,
  Terminal,
} from "lucide-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Favicon } from "../Favicon";
import { CodeDiagnosticsResult } from "./CodeDiagnosticsResult";
import { SearchHitResult } from "./SearchHitResult";
import { codeDiagnosticsPeek, extractCodeDiagnostics } from "./codeDiagnostics";
import { type DiffLine, lineDiff } from "./diff";
import { isFileReadCeilingGuidance } from "./fileReadCeiling";
import { isSearchHitTool } from "./parseSearchHits";
import { specificToolFailureMessage } from "./productFailureFace";
import { isVerifyBudgetExceeded } from "./verifyBudget";

/** Normalized data a tool result renders from, shared by the single-agent process
 * panel (ProcessToolRow) and the multi-agent run detail (RunDetailBody): the call
 * `args`, the model-facing `result` text, optional rich `display`, and optional
 * product `failure` face (展开详情用；折叠行保持一行). */
export interface ToolResultData {
  toolName: string;
  args: Record<string, unknown>;
  result: string | null;
  display?: ToolDisplay | null;
  /** Present on status=error or status=redirect when the server sent `tool_use_end.failure`. */
  failure?: ToolFailure | null;
  status: "running" | "success" | "error" | "redirect";
  /** Conversation the call belongs to — only the browser result uses it, to lazy-fetch
   * its key-frame from that conversation's workspace. Absent everywhere else. */
  conversationId?: string | null;
}

function asString(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function isWebSearchDisplay(d: unknown): d is WebSearchDisplay {
  return !!d && Array.isArray((d as { results?: unknown }).results);
}

function isReadUrlDisplay(d: unknown): d is ReadUrlDisplay {
  if (!d) return false;
  const x = d as { url?: unknown; content?: unknown };
  return typeof x.url === "string" && typeof x.content === "string";
}

function isCodeExecDisplay(d: unknown): d is CodeExecDisplay {
  if (!d) return false;
  const x = d as { stdout?: unknown; stderr?: unknown; exit_code?: unknown };
  return (
    typeof x.stdout === "string" ||
    typeof x.stderr === "string" ||
    typeof x.exit_code === "number"
  );
}

function isSkillConsultDisplay(d: unknown): d is SkillConsultDisplay {
  return !!d && typeof (d as { skill_name?: unknown }).skill_name === "string";
}

function isMemoryConsultDisplay(d: unknown): d is MemoryConsultDisplay {
  return !!d && typeof (d as { topic?: unknown }).topic === "string";
}

function isRuleConsultDisplay(d: unknown): d is RuleConsultDisplay {
  return !!d && typeof (d as { rule?: unknown }).rule === "string";
}

/** Unified `consult` display: `{name}` (+ optional `reused` / source `kind`).
 * Folder-command displays also carry `name` (resolve/create/delete_folder) —
 * only genuine consult keys may match. */
const CONSULT_DISPLAY_KEYS = new Set(["name", "reused", "kind"]);

function isUnifiedConsultDisplay(d: unknown): d is UnifiedConsultDisplay {
  if (!d || typeof d !== "object") return false;
  const x = d as Record<string, unknown>;
  if (typeof x.name !== "string") return false;
  for (const k of Object.keys(x)) {
    if (!CONSULT_DISPLAY_KEYS.has(k)) return false;
  }
  if (typeof x.kind === "string") {
    if (x.kind !== "memory" && x.kind !== "skill" && x.kind !== "rule") {
      return false;
    }
  }
  return true;
}

function isListFoldersCountDisplay(d: unknown): d is { count: number } {
  if (!d || typeof d !== "object") return false;
  const x = d as Record<string, unknown>;
  return typeof x.count === "number" && Number.isFinite(x.count);
}

/** `search_conversations` / `read_conversation` display — metadata only (body in result). */
function isConversationLogDisplay(d: unknown): d is ConversationLogDisplay {
  if (!d || typeof d !== "object") return false;
  const x = d as Record<string, unknown>;
  // Exclude sibling rich displays that share open-dict `display`.
  if (
    "topic" in x ||
    "skill_name" in x ||
    "rule" in x ||
    "name" in x ||
    "results" in x ||
    "url" in x ||
    "stdout" in x ||
    "kind" in x
  ) {
    return false;
  }
  if (typeof x.conversation_id === "string") return true;
  if (typeof x.result_count === "number") return true;
  return false;
}

/** Whether a tool has anything to expand — a rich display, an editable diff, or a
 * non-empty text result. Drives ProcessToolRow's click-to-expand affordance. */
export function hasToolResultBody(d: ToolResultData): boolean {
  if (d.status === "running") return false;
  if (d.status === "redirect") {
    return Boolean(d.failure?.message?.trim());
  }
  // Successful handoff: expandable only when the brief has details (not summary-only).
  // The protocol receipt is never a body.
  if (isSuccessfulHandoff(d.toolName, d.status)) {
    return hasDebriefDetails(debriefFromHandoffArgs(d.args));
  }
  // Successful wait: receipt-only — one line, no chevron (same as summary-only handoff).
  if (d.toolName === "wait" && d.status === "success") return false;
  if (d.display) return true;
  if (isFileEdit(d)) return true;
  if (isFileWrite(d)) return true;
  if (specificToolFailureMessage(d)) return true;
  return !!d.result?.trim();
}

function isFileEdit(d: ToolResultData): boolean {
  return (
    d.status === "success" &&
    d.toolName === "str_replace" &&
    asString(d.args.old_string) !== null &&
    asString(d.args.new_string) !== null
  );
}

function isFileWrite(d: ToolResultData): boolean {
  return (
    d.status === "success" &&
    d.toolName === "file_write" &&
    asString(d.args.content) !== null
  );
}

/** A compact one-line peek for the collapsed row — display-aware so it reads as
 * 「3 results」/「exit 1」rather than the first line of a JSON / "stdout:" blob.
 * Collapsed error rows stay one line (title + red ✗): product `failure.message`
 * is not peeked here (specific copy lives in the expanded detail; generic copy
 * is hidden). Display-derived summaries still apply; expand shows technical
 * `result`. Absent `failure` (旧服务端 / 历史 journal) keeps the legacy
 * result-first-line fallback when a display summary is not available. */
export function toolResultPeek(d: ToolResultData): string {
  if (isSuccessfulHandoff(d.toolName, d.status)) {
    return clampLine(handoffSummaryPeek(d.args));
  }
  if (isWebSearchDisplay(d.display)) {
    const n = d.display.results.length;
    return n > 0 ? `${n} result${n === 1 ? "" : "s"}` : "No results";
  }
  if (d.toolName === "list_folders" && isListFoldersCountDisplay(d.display)) {
    const n = d.display.count;
    return `${n} folder${n === 1 ? "" : "s"}`;
  }
  if (isReadUrlDisplay(d.display)) {
    const title =
      cleanSourceTitle(d.display.title) || d.display.site || d.display.url;
    const site = d.display.site?.trim();
    if (site && title !== site) return clampLine(`${title} · ${site}`);
    return clampLine(title);
  }
  if (isCodeExecDisplay(d.display)) {
    if (isVerifyBudgetExceeded(d.display)) return "验证未完成（预算耗尽）";
    const code =
      typeof d.display.exit_code === "number" ? d.display.exit_code : 0;
    if (code !== 0) return `退出码 ${code}`;
    return "";
  }
  if (isSkillConsultDisplay(d.display)) {
    return clampLine(d.display.summary || "已查阅能力指引");
  }
  if (isMemoryConsultDisplay(d.display)) {
    return clampLine(d.display.topic || "已查阅记忆");
  }
  if (isRuleConsultDisplay(d.display)) {
    return clampLine(d.display.rule || "已查阅规则");
  }
  if (d.toolName === "consult" && isUnifiedConsultDisplay(d.display)) {
    return clampLine(d.display.name || "已查阅");
  }
  if (isConversationLogDisplay(d.display)) {
    if (typeof d.display.conversation_id === "string") {
      const title = d.display.title?.trim();
      if (title) {
        return clampLine(d.display.truncated ? `${title} · 已截断` : title);
      }
      return d.display.truncated ? "已截断" : "已查阅对话";
    }
    if (typeof d.display.result_count === "number") {
      const n = d.display.result_count;
      return `${n} 场对话`;
    }
    return "已查阅对话";
  }
  if (isBrowserDisplay(d.display)) {
    return browserResultPeek(d.display);
  }
  // Inner-loop diagnostics peek (「N 个类型错误」) — before write path labels so
  // a write that attached diagnostics surfaces the error count when present.
  const diagPeek = extractCodeDiagnostics(d.display);
  if (diagPeek) {
    const peek = codeDiagnosticsPeek(diagPeek);
    if (diagPeek.status === "unavailable" || peek !== "未发现类型错误") {
      return clampLine(peek);
    }
    // Clean diagnostics on a write → keep「已写入 path」below; standalone → clean.
    if (!isFileEdit(d) && !isFileWrite(d)) return peek;
  }
  if (isFileEdit(d)) {
    const path = asString(d.args.path);
    return path ? `已编辑 ${path}` : "已编辑";
  }
  if (isFileWrite(d)) {
    const path = asString(d.args.path);
    return path ? `已写入 ${path}` : "已写入文件";
  }
  if (
    d.status === "error" &&
    !isFileReadCeilingGuidance(d.toolName, d.result)
  ) {
    return "";
  }
  if (d.toolName === "grep") return grepCollapsedPeek(d.result);
  const line = (d.result ?? "").split("\n").find((l) => l.trim()) ?? "";
  return clampLine(line);
}

/** Collapsed grep meta — pattern already lives in the ToolLine title.
 * Unknown shapes stay empty; expand the row to read hits. */
function grepCollapsedPeek(result: string | null): string {
  const line =
    (result ?? "")
      .split("\n")
      .find((l) => l.trim())
      ?.trim() ?? "";
  if (!line) return "";
  const hits = line.match(/^(\d+) 处匹配，分布在 (\d+) 个文件/);
  if (hits) return `${hits[1]} 处匹配 · ${hits[2]} 个文件`;
  const files = line.match(/^(\d+) 个文件匹配/);
  if (files) return `${files[1]} 个文件`;
  if (line.startsWith("本次 grep 未匹配")) return "未匹配";
  // Pattern already lives in the ToolLine title. Unknown shapes (raw hits,
  // head-tail chops) must not paste another 140 chars of regex onto the row.
  return "";
}

function clampLine(line: string): string {
  return line.length > 140 ? `${line.slice(0, 140)}…` : line;
}

/** Search hits as source-style cards (favicon · title · snippet), each opening in
 * the system browser — mirrors {@link SourceCards} so a search step reads the same
 * as the answer's sources. */
function WebSearchResult({ display }: { display: WebSearchDisplay }) {
  const query = asString(display.query);
  return (
    <div className="mt-1 space-y-1">
      {query && (
        <div className="px-1 text-xs text-muted-foreground">搜索：{query}</div>
      )}
      <div className="flex max-h-72 flex-col gap-0.5 overflow-y-auto pr-1">
        {display.results.map((r, i) => (
          <a
            key={`${r.url}-${i}`}
            href={r.url}
            target="_blank"
            rel="noreferrer"
            className="flex items-start gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-accent"
          >
            <span className="mt-0.5 w-4 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
              {i + 1}
            </span>
            <Favicon
              site={r.site}
              title={r.title}
              size={16}
              className="mt-0.5"
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium text-foreground">
                {cleanSourceTitle(r.title) || r.site || r.url}
              </span>
              {r.snippet && (
                <span className="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">
                  {r.snippet}
                </span>
              )}
            </span>
          </a>
        ))}
      </div>
    </div>
  );
}

/** Single-page read card (工具结果富渲染): a source-style header (favicon · title ·
 * site, opens in the system browser) plus the extracted body preview — mirrors
 * {@link WebSearchResult} / {@link SourceCards} for the header and the bordered
 * header+body shell of {@link SkillConsultResult}. */
function ReadUrlResult({ display }: { display: ReadUrlDisplay }) {
  const title = cleanSourceTitle(display.title) || display.site || display.url;
  const body = (display.content ?? "").replace(/\n+$/, "");
  return (
    <div className="mt-1 overflow-hidden rounded-lg border border-border">
      <a
        href={display.url}
        target="_blank"
        rel="noreferrer"
        className="flex items-start gap-2 border-border/60 border-b bg-muted/40 px-2.5 py-1.5 transition-colors hover:bg-accent"
      >
        <Favicon
          site={display.site}
          title={display.title}
          size={16}
          className="mt-0.5"
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-foreground">
            {title}
          </span>
          {display.site && (
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {display.site}
            </span>
          )}
        </span>
      </a>
      <div className="max-h-72 overflow-auto bg-muted/30 px-3 py-2 text-xs leading-relaxed">
        {body ? (
          <pre className="whitespace-pre-wrap break-words text-foreground/90">
            {body}
          </pre>
        ) : (
          <span className="text-muted-foreground/60">（无正文）</span>
        )}
      </div>
    </div>
  );
}

/** Terminal-style stdout/stderr view + exit-code badge.
 *  Hard fail → stderr destructive; ``budget_exceeded`` → incomplete banner + muted/warning
 *  (Timeout stderr is not painted as fault red). */
function CodeExecResult({ display }: { display: CodeExecDisplay }) {
  const exitCode =
    typeof display.exit_code === "number" ? display.exit_code : 0;
  const incomplete = isVerifyBudgetExceeded(display);
  const failed = !incomplete && exitCode !== 0;
  const stdout = (display.stdout ?? "").replace(/\n+$/, "");
  const stderr = (display.stderr ?? "").replace(/\n+$/, "");
  const empty = !stdout && !stderr;
  const exitTone = incomplete
    ? "text-warning"
    : failed
      ? "text-destructive"
      : "text-success";
  return (
    <div className="mt-1 overflow-hidden rounded-lg border border-border">
      {incomplete && (
        <div className="border-border/60 border-b bg-warning/10 px-2.5 py-1.5 text-xs text-warning">
          验证未完成（预算耗尽）
        </div>
      )}
      <div className="flex items-center gap-2 border-border/60 border-b bg-muted/40 px-2.5 py-1 text-xs">
        <Terminal size={12} className="shrink-0 text-muted-foreground" />
        <span className="text-muted-foreground">
          {display.language || display.check || "shell"}
        </span>
        <span className={`ml-auto tabular-nums ${exitTone}`}>
          退出码 {exitCode}
        </span>
      </div>
      <div className="max-h-72 overflow-auto bg-muted/30 px-3 py-2 font-mono text-xs leading-relaxed">
        {empty && <span className="text-muted-foreground/60">（无输出）</span>}
        {stdout && (
          <pre className="whitespace-pre-wrap break-words text-foreground/90">
            {stdout}
          </pre>
        )}
        {stderr && (
          <pre
            className={`mt-1 whitespace-pre-wrap break-words ${
              incomplete ? "text-muted-foreground" : "text-destructive"
            }`}
          >
            {stderr}
          </pre>
        )}
      </div>
    </div>
  );
}

/** Pulled system-skill card (渐进披露 可视化): the catalog name + one-line summary as
 * a header, with the full guidance body the CEO consulted shown verbatim below — so
 * the user sees exactly which capability the AI reached for and what it read. */
function SkillConsultResult({
  display,
  result,
}: {
  display: SkillConsultDisplay;
  result: string;
}) {
  return (
    <div className="mt-1 overflow-hidden rounded-lg border border-border">
      <div className="flex items-center gap-2 border-border/60 border-b bg-muted/40 px-2.5 py-1 text-xs">
        <BookOpen size={12} className="shrink-0 text-muted-foreground" />
        <span className="truncate font-mono text-foreground">
          {display.skill_name}
        </span>
        <span className="ml-auto shrink-0 text-muted-foreground">能力指引</span>
      </div>
      {display.summary && (
        <div className="border-border/60 border-b px-2.5 py-1.5 text-xs text-muted-foreground">
          {display.summary}
        </div>
      )}
      {result.trim() && (
        <div className="px-1 pb-1">
          <PromptDocument text={result} maxHeightClass="max-h-72" />
        </div>
      )}
    </div>
  );
}

/** Pulled memory-topic card (记忆文件夹化 §六 · 渐进披露 可视化): the topic name as a
 * header, with the full note body the CEO consulted shown verbatim below — so the user
 * sees exactly which memory the AI reached for and what it read. Mirrors
 * {@link SkillConsultResult}, the sibling on-demand consult. */
function MemoryConsultResult({
  display,
  result,
}: {
  display: MemoryConsultDisplay;
  result: string;
}) {
  return (
    <div className="mt-1 overflow-hidden rounded-lg border border-border">
      <div className="flex items-center gap-2 border-border/60 border-b bg-muted/40 px-2.5 py-1 text-xs">
        <Brain size={12} className="shrink-0 text-muted-foreground" />
        <span className="text-muted-foreground">查阅记忆：</span>
        <span className="truncate font-mono text-foreground">
          {display.topic}
        </span>
      </div>
      {result.trim() && (
        <div className="px-1 pb-1">
          <PromptDocument text={result} maxHeightClass="max-h-72" />
        </div>
      )}
    </div>
  );
}

/** Preview cap for conversation-log tool cards — full transcript stays in `result`
 * for the model; the UI truncates so a huge read doesn't flood the expand panel.
 * Aligns with the display wire-cap (~6000) discipline (跨会话对话日志定案). */
const CONVERSATION_LOG_PREVIEW_CHARS = 6000;

/** Worker conversation-log card (`search_conversations` / `read_conversation`):
 * display carries title / id / truncated / result_count; body from `result`.
 * Mirrors {@link MemoryConsultResult}. */
function ConversationLogResult({
  display,
  result,
}: {
  display: ConversationLogDisplay;
  result: string;
}) {
  const navigate = useNavigate();
  const isRead = typeof display.conversation_id === "string";
  const conversationId = isRead ? display.conversation_id : undefined;
  const title = display.title?.trim();
  const preview =
    result.length > CONVERSATION_LOG_PREVIEW_CHARS
      ? `${result.slice(0, CONVERSATION_LOG_PREVIEW_CHARS)}\n…`
      : result;
  const previewClipped = result.length > CONVERSATION_LOG_PREVIEW_CHARS;

  return (
    <div className="mt-1 overflow-hidden rounded-lg border border-border">
      <div className="flex items-center gap-2 border-border/60 border-b bg-muted/40 px-2.5 py-1 text-xs">
        <MessagesSquare size={12} className="shrink-0 text-muted-foreground" />
        {isRead ? (
          <>
            <span className="text-muted-foreground">查阅对话：</span>
            <span className="min-w-0 truncate font-medium text-foreground">
              {title || display.conversation_id || "（无标题）"}
            </span>
            {display.truncated && (
              <span className="shrink-0 text-warning">已截断</span>
            )}
            {conversationId && (
              <button
                type="button"
                onClick={() => navigate(`/conversations/${conversationId}`)}
                className="ml-auto shrink-0 text-xs text-muted-foreground hover:text-foreground hover:underline"
              >
                打开对话
              </button>
            )}
          </>
        ) : (
          <>
            <span className="text-muted-foreground">检索对话</span>
            <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">
              {display.result_count ?? 0} 场
              {display.scope ? ` · ${display.scope}` : ""}
            </span>
          </>
        )}
      </div>
      {isRead && conversationId && (
        <div className="border-border/60 border-b px-2.5 py-1 font-mono text-xs text-muted-foreground">
          {conversationId}
        </div>
      )}
      {preview.trim() && (
        <div className="px-1 pb-1">
          <PromptDocument text={preview} maxHeightClass="max-h-72" />
        </div>
      )}
      {previewClipped && (
        <div className="border-border/60 border-t bg-muted/40 px-2.5 py-1 text-muted-foreground text-xs">
          预览已截断（完整内容在工具结果中，可续读拼回）
        </div>
      )}
    </div>
  );
}

function diffSign(type: DiffLine["type"]): string {
  if (type === "add") return "+";
  if (type === "del") return "-";
  return " ";
}

function diffRowClass(type: DiffLine["type"]): string {
  if (type === "add") return "bg-success/10 text-foreground";
  if (type === "del") return "bg-destructive/10 text-foreground";
  return "text-muted-foreground";
}

/** Red/green line diff for a str_replace edit, derived from the call arguments
 * (old_string → new_string) the client already has — no backend echo needed. */
function FileEditDiff({
  path,
  oldStr,
  newStr,
}: {
  path: string | null;
  oldStr: string;
  newStr: string;
}) {
  const lines = useMemo(() => lineDiff(oldStr, newStr), [oldStr, newStr]);
  const adds = lines.reduce((n, l) => (l.type === "add" ? n + 1 : n), 0);
  const dels = lines.reduce((n, l) => (l.type === "del" ? n + 1 : n), 0);
  return (
    <div className="mt-1 overflow-hidden rounded-lg border border-border">
      <div className="flex items-center gap-2 border-border/60 border-b bg-muted/40 px-2.5 py-1 text-xs">
        <FileCode2 size={12} className="shrink-0 text-muted-foreground" />
        {path && (
          <span className="truncate font-mono text-foreground">{path}</span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-1.5 tabular-nums">
          <span className="text-success">+{adds}</span>
          <span className="text-destructive">-{dels}</span>
        </span>
      </div>
      <div className="max-h-72 overflow-auto font-mono text-xs leading-relaxed">
        {lines.map((l, i) => (
          <div
            // biome-ignore lint/suspicious/noArrayIndexKey: a diff render is a stable, positional list — the index is the natural key and rows never reorder within a render.
            key={i}
            className={`flex ${diffRowClass(l.type)}`}
          >
            <span className="w-5 shrink-0 select-none text-center text-muted-foreground/50">
              {diffSign(l.type)}
            </span>
            <span className="whitespace-pre-wrap break-words pr-2">
              {l.text || " "}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** How many lines of a written file to render before truncating with a footer —
 * file_write content is uncapped in the call args, so the preview is bounded here. */
const FILE_WRITE_PREVIEW_LINES = 300;

/** New/overwritten file card for file_write, with a line-numbered content preview
 * — built from the call's `content` argument (already client-side), no backend
 * echo needed. Neutral framing (we can't tell create from overwrite without an
 * extra probe), so it reads as「写入 N 行到 path」. */
function FileWriteCard({
  path,
  content,
}: {
  path: string | null;
  content: string;
}) {
  const allLines = content.split("\n");
  const shown = allLines.slice(0, FILE_WRITE_PREVIEW_LINES);
  const hidden = allLines.length - shown.length;
  return (
    <div className="mt-1 overflow-hidden rounded-lg border border-border">
      <div className="flex items-center gap-2 border-border/60 border-b bg-muted/40 px-2.5 py-1 text-xs">
        <FileText size={12} className="shrink-0 text-muted-foreground" />
        {path && (
          <span className="truncate font-mono text-foreground">{path}</span>
        )}
        <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">
          {allLines.length} 行 · {content.length} 字
        </span>
      </div>
      <div className="max-h-72 overflow-auto font-mono text-xs leading-relaxed">
        {shown.map((line, i) => (
          <div
            // biome-ignore lint/suspicious/noArrayIndexKey: a file preview is a stable, positional line list — the index is the natural key and rows never reorder within a render.
            key={i}
            className="flex"
          >
            <span className="w-8 shrink-0 select-none pr-2 text-right text-muted-foreground/40">
              {i + 1}
            </span>
            <span className="whitespace-pre-wrap break-words pr-2 text-foreground/90">
              {line || " "}
            </span>
          </div>
        ))}
      </div>
      {hidden > 0 && (
        <div className="border-border/60 border-t bg-muted/40 px-2.5 py-1 text-muted-foreground text-xs">
          … 还有 {hidden} 行（共 {allLines.length} 行）
        </div>
      )}
    </div>
  );
}

/** Plain text fallback (the prior `<pre>` body) for tools without a rich view. */
function TextResult({
  result,
  status,
  ceilingGuidance = false,
}: {
  result: string;
  status: ToolResultData["status"];
  /** Same-path file_read ceiling — warning/muted, not fault red. */
  ceilingGuidance?: boolean;
}) {
  const tone = ceilingGuidance
    ? "text-warning/90"
    : status === "error"
      ? "text-destructive/90"
      : "text-muted-foreground";
  return (
    <pre
      className={`mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/50 px-2 py-1.5 text-xs ${tone}`}
    >
      {result}
    </pre>
  );
}

/**
 * Rich rendering of a finished tool call (工具结果富渲染), keyed off the tool name
 * (形状是数据不是模式): web_search → result cards, read_url → source card + body,
 * code_execute → a terminal view, str_replace → a red/green diff, file_write → a
 * content card (the last two from the call args). Anything else — or a tool whose
 * rich data is absent — falls back to the model-facing text result.
 */
export function ToolResultView({ data }: { data: ToolResultData }) {
  if (data.status === "redirect") {
    const message = data.failure?.message?.trim();
    if (!message) return null;
    return (
      <p
        className="mt-1 text-xs text-muted-foreground"
        data-testid="tool-channel-redirect"
      >
        {message}
      </p>
    );
  }
  const face = specificToolFailureMessage(data);
  const body = <ToolResultBody data={data} />;
  if (!face) return body;
  return (
    <div>
      <p
        className="mt-1 truncate text-xs text-muted-foreground"
        data-testid="tool-product-failure"
      >
        {face}
      </p>
      {body}
    </div>
  );
}

function ToolResultBody({ data }: { data: ToolResultData }) {
  const diagnostics = extractCodeDiagnostics(data.display);

  if (isSuccessfulHandoff(data.toolName, data.status)) {
    const debrief = debriefFromHandoffArgs(data.args);
    if (!hasDebriefDetails(debrief)) return null;
    return (
      <div className="mt-1">
        <DebriefDetails debrief={debrief} />
      </div>
    );
  }

  if (isWebSearchDisplay(data.display)) {
    return <WebSearchResult display={data.display} />;
  }
  if (isReadUrlDisplay(data.display)) {
    return <ReadUrlResult display={data.display} />;
  }
  if (isCodeExecDisplay(data.display)) {
    return <CodeExecResult display={data.display} />;
  }
  if (isSkillConsultDisplay(data.display)) {
    return (
      <SkillConsultResult display={data.display} result={data.result ?? ""} />
    );
  }
  if (isMemoryConsultDisplay(data.display)) {
    return (
      <MemoryConsultResult display={data.display} result={data.result ?? ""} />
    );
  }
  if (isRuleConsultDisplay(data.display)) {
    // Historical consult_rule: same card as consult_memory / unified consult.
    return (
      <MemoryConsultResult
        display={{ topic: data.display.rule }}
        result={data.result ?? ""}
      />
    );
  }
  if (data.toolName === "consult" && isUnifiedConsultDisplay(data.display)) {
    // Unified consult: same card as consult_memory (条目名 + 正文).
    return (
      <MemoryConsultResult
        display={{ topic: data.display.name }}
        result={data.result ?? ""}
      />
    );
  }
  if (isConversationLogDisplay(data.display)) {
    return (
      <ConversationLogResult
        display={data.display}
        result={data.result ?? ""}
      />
    );
  }
  if (isBrowserDisplay(data.display)) {
    return (
      <BrowserResult
        display={data.display}
        conversationId={data.conversationId ?? null}
      />
    );
  }
  if (isFileEdit(data)) {
    return (
      <div>
        <FileEditDiff
          path={asString(data.args.path)}
          oldStr={asString(data.args.old_string) ?? ""}
          newStr={asString(data.args.new_string) ?? ""}
        />
        {diagnostics && <CodeDiagnosticsResult display={diagnostics} />}
      </div>
    );
  }
  if (isFileWrite(data)) {
    return (
      <div>
        <FileWriteCard
          path={asString(data.args.path)}
          content={asString(data.args.content) ?? ""}
        />
        {diagnostics && <CodeDiagnosticsResult display={diagnostics} />}
      </div>
    );
  }
  // Standalone / write-tool-attached diagnostics (not a write preview above).
  if (diagnostics) {
    return <CodeDiagnosticsResult display={diagnostics} />;
  }
  // grep / code_search: clickable workspace paths → side-panel file preview.
  // Empty「可执行下一步」notes have no hit lines → plain TextResult below.
  if (
    data.status === "success" &&
    isSearchHitTool(data.toolName) &&
    data.result?.trim()
  ) {
    return <SearchHitResult result={data.result} kind={data.toolName} />;
  }
  const ceilingGuidance = isFileReadCeilingGuidance(data.toolName, data.result);
  return (
    <TextResult
      result={data.result ?? ""}
      status={data.status}
      ceilingGuidance={ceilingGuidance}
    />
  );
}
