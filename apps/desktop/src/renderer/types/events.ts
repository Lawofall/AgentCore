// Desktop SSE 契约类型 = 共享单一源 @agentcore/contract-types 的 re-export（手机端落地
// 设计 §六 支柱2）。桌面已并入 workspace，本文件不再各自定义这些 wire 类型，改用
// `export type *` 透传共享单一源（纯类型，编译期 erase，运行时零引用 contract-types）。
// 后端加事件类型 → 改 contract-types 一处 → 桌面/手机两端编译失败直到处理（漂移绊线）。
//
// 仅保留 desktop 独有的「工具结果富渲染」narrow 类型：它们是桌面渲染层细节（手机精简端
// 不需要、不属于跨端 wire 契约），故留在本地而非下沉共享包。
export type * from "@agentcore/contract-types";

/** One web hit in a `web_search` tool's structured display (工具结果富渲染): a
 * result card's data (favicon via `site` · `title` · `snippet`). */
export interface WebSearchHit {
  title: string;
  url: string;
  snippet: string;
  /** Display host (sans www.), parsed server-side so the card needs no URL work. */
  site?: string;
}

/** `web_search` rich result: the query + its hits, shown as source-style cards. */
export interface WebSearchDisplay {
  query: string;
  results: WebSearchHit[];
}

/** `read_url` rich result (工具结果富渲染): a single source-style card header
 * (favicon · title · site) plus the extracted page body for the expandable
 * preview. Mirrors citation fields so it visually aligns with WebSearchResult /
 * SourceCards; the client never parses the model-facing JSON `result`. */
export interface ReadUrlDisplay {
  url: string;
  title: string;
  /** Display host (sans www.), parsed server-side so the card needs no URL work. */
  site?: string;
  snippet?: string;
  /** Extracted main text (may be size-capped on the wire via `_cap_display`). */
  content: string;
}

/** `code_execute` / `test_run` / `run` rich result: terminal-style stdout/stderr + exit code.
 *  ``budget_exceeded`` = incomplete (not a hard tool fault). Face copy branches on
 *  ``timeout_kind`` (idle / disaster). */
export interface CodeExecDisplay {
  stdout: string;
  stderr: string;
  exit_code: number;
  language?: string;
  /** True when incomplete — UI shows warning, not fault red. */
  budget_exceeded?: boolean;
  /** Idle hang vs disaster wall; omit or null when not a timeout incomplete. */
  timeout_kind?: "idle" | "disaster" | null;
  check?: string;
  command?: string;
}

/** `consult_skill` rich result (渐进披露 可视化): which system「能力」the CEO pulled
 * — its catalog `skill_name` + the one-line `summary`. The full guidance body rides
 * the `result` text. Name lives on the ToolLine; expand is body-only (plus summary).
 * Kept for historical journal replay after the unified `consult` tool landed. */
export interface SkillConsultDisplay {
  skill_name: string;
  summary: string;
}

/** `consult_memory` rich result (记忆文件夹化 §六 · 渐进披露 可视化): which 记忆主题笔记
 * the CEO pulled — its `topic` name. Kept for historical journal replay. */
export interface MemoryConsultDisplay {
  topic: string;
}

/** `consult_rule` rich result (historical journal): on-demand user-rule name.
 * Name lives on the ToolLine; expand is body-only (same as unified `origin=user`). */
export interface RuleConsultDisplay {
  rule: string;
}

/** Unified `consult` rich result (按需三合一): entry `name` + optional two-bucket
 * `origin` (`system` manuals vs `user` 设定). Skill / rule / memory 三分不进 display。
 * Name lives on the ToolLine; expand is body-only (same as historical consult_*). */
export interface UnifiedConsultDisplay {
  name: string;
  reused?: boolean;
  origin?: "system" | "user";
}

/**
 * `search_conversations` / `read_conversation` rich result (跨会话对话日志 · 工具卡):
 * metadata only in `display` (title / conversation_id / truncated / result_count) — the
 * transcript or hit list rides `result` so display stays under the ~6000-char wire cap.
 * Search typically sets `result_count` (+ optional `scope`); read sets `title` /
 * `conversation_id` / `truncated` (+ optional `depth: "dialogue" | "process"`).
 */
export interface ConversationLogDisplay {
  title?: string;
  conversation_id?: string;
  truncated?: boolean;
  result_count?: number;
  scope?: string;
  depth?: string;
}

/** The action verb a single `browser_*` step performed (L3 团队浏览器 M0). Kept a
 * closed union for icon/label mapping, but the guard accepts any string so a
 * newer-backend verb degrades to a generic row instead of vanishing. */
export type BrowserAction =
  | "navigate"
  | "click"
  | "type"
  | "scroll"
  | "snapshot"
  | "screenshot";

/**
 * `browser_*` rich result (L3「团队浏览器」M0 · 关键帧活动卡): one worker browser step's
 * DURABLE display — the `action` verb, the page `url` (+ optional `title`) at that step,
 * a human-readable `detail`, and an optional key-frame `frame` (a jpeg workspace_path
 * such as `browser/step-0007.jpg`, lazy-fetched from the conversation workspace when the
 * card expands). Rides `tool_use_end.display` (落 journal), so the activity card rebuilds
 * verbatim on reload / journal replay — never sourced from the live-only tool_use_progress.
 */
export interface BrowserDisplay {
  kind: "browser";
  action: BrowserAction | string;
  url: string;
  title?: string;
  detail?: string;
  frame?: string;
  /** Registry session_id — 成功路径由后端写入，供右坞 upsert 绑页。 */
  session_id?: string;
  /** ``local`` | ``sandbox`` — 与 session_id 同路推送。 */
  host_kind?: "local" | "sandbox" | string;
}

/** Non-process Host result (status / os_log / settings). Process envelopes
 * (`stdout` / `exit_code`) reuse {@link CodeExecDisplay} instead. */
export interface HostDisplay {
  kind: "host";
  action: string;
  body: string;
}
