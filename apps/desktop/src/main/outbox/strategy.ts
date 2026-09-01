/**
 * Outbox writeback — pure strategy / path / record IO helpers.
 *
 * Mirrors server ``normalize_local_turn_tool_failure_code`` etc.; no drain loop.
 */
import {
  mkdir,
  readFile,
  readdir,
  rename,
  unlink,
  writeFile,
} from "node:fs/promises";
import { join } from "node:path";
import { app } from "electron";

export const PHASE_READY = "ready";
export const PHASE_OPEN = "open";

const CHANNEL_CAPTAIN_CONTENT = "captain:content";
const CHANNEL_CAPTAIN_REASONING = "captain:reasoning";

/** Per-record writeback backoff: base 2s, double each failure, cap 5 min + jitter. */
const BACKOFF_BASE_MS = 2_000;
const BACKOFF_MAX_MS = 5 * 60_000;

/**
 * Same spirit as sidecar `_is_safe_id`: reject empty, `..` traversal, and
 * path separators before joining filesystem paths or URL segments.
 */
export function isSafeOutboxId(value: string): boolean {
  if (typeof value !== "string" || !value || value.includes("..")) return false;
  return !value.includes("/") && !value.includes("\\") && !value.includes("\0");
}

/** Desktop-minted local-turn trace: 32 hex, same as server `new_trace_id`. */
export function isHex32TraceId(value: string): boolean {
  return typeof value === "string" && /^[0-9a-f]{32}$/i.test(value);
}

let tmpSeq = 0;

/** Unique temp path — a shared `*.json.tmp` lets a concurrent writer steal our rename. */
export function tmpPathFor(target: string): string {
  tmpSeq += 1;
  return `${target}.${process.pid}-${tmpSeq}.tmp`;
}

export function sidecarDataDir(): string {
  return join(app.getPath("userData"), "sidecar");
}

export function outboxDir(): string {
  return join(sidecarDataDir(), "outbox");
}

export function pausedDir(): string {
  return join(sidecarDataDir(), "paused");
}

export function deadLetterDir(): string {
  return join(sidecarDataDir(), "dead-letter");
}

export interface OutboxRecord {
  schema_version?: number;
  user_message_id: string;
  conversation_id: string;
  message_id?: string | null;
  trace_id?: string;
  user_message?: string;
  content?: string;
  reasoning_content?: string | null;
  citations?: unknown[];
  runs?: unknown;
  /** seq(str) → {kind,payload,ts,ord?} — progressive journal; crash salvage has no runs.
   *  ``ord`` is emission order (outbox twin of Postgres ``created_at``). JS
   *  ``JSON.parse`` reorders integer-like keys, so readers must sort by ``ord``.
   */
  journal?: Record<string, unknown>;
  /**
   * Mid-stream channel snapshots from StreamCheckpointer (D6).
   * channel → { text, generation }; desktop restart salvage reads captain:* when content is empty.
   */
  stream_segments?: Record<string, { text?: string; generation?: number }>;
  input_tokens?: number;
  output_tokens?: number;
  reasoning_tokens?: number;
  cache_hit_tokens?: number;
  cache_miss_tokens?: number;
  rounds?: number;
  finish_reason?: string | null;
  phase?: string;
  updated_at?: number;
  /**
   * Desktop-owned retry bookkeeping (optional). Absent on fresh sidecar files;
   * written by writebacker after transient failures.
   */
  retry_count?: number;
  /** Epoch ms — skip POST until this time (unless flushTurn bypasses). */
  next_attempt_at?: number;
  /**
   * RecordTurnRequest provenance (sidecar harvest). Optional; ordinary turns omit.
   * Wire names stay snake_case to match POST `/local-turns`.
   */
  origin?: string | null;
  execution_id?: string | null;
  harvest_kind?: string | null;
  /** Soft @Agent chips on the local user bubble (optional; old records omit). */
  agent_mentions?: Array<{ agent_id: string; role: string }>;
  /**
   * Pause-turn journal watermark stamped on ``reopen_for_resume``.
   * Write-back keeps the full journal on the wire and only filters
   * ``tool_failures`` to seqs strictly after this value.
   */
  resume_after_seq?: number;
}

/**
 * HTTP 4xx except 401/408/429 → permanent (dead-letter).
 * 401/408/429/5xx/network (status 0) → transient (backoff retry).
 */
export function isPermanentHttpFailure(status: number): boolean {
  if (status < 400 || status >= 500) return false;
  return status !== 401 && status !== 408 && status !== 429;
}

/** Delay after `retryCount` failures (1-based). Caps at 5 min; adds up to 25% jitter. */
export function computeBackoffDelayMs(
  retryCount: number,
  random: () => number = Math.random,
): number {
  const failures = Math.max(1, retryCount);
  const exp = Math.min(failures - 1, 20);
  const base = Math.min(BACKOFF_BASE_MS * 2 ** exp, BACKOFF_MAX_MS);
  const jitter = Math.floor(random() * base * 0.25);
  return base + jitter;
}

/** Same key as Python ``JOURNAL_ENTRY_ORD_KEY`` — emission index on the entry. */
const JOURNAL_ENTRY_ORD_KEY = "ord";

function entryOrd(value: unknown): number | null {
  if (!value || typeof value !== "object") return null;
  const raw = (value as Record<string, unknown>)[JOURNAL_ENTRY_ORD_KEY];
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  return raw;
}

function stripEntryOrd(value: unknown): unknown {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  if (!Object.prototype.hasOwnProperty.call(value, JOURNAL_ENTRY_ORD_KEY)) {
    return value;
  }
  const copy = { ...(value as Record<string, unknown>) };
  delete copy[JOURNAL_ENTRY_ORD_KEY];
  return copy;
}

function isNumericJournalKey(key: string): boolean {
  if (!key) return false;
  const n = Number(key);
  return Number.isFinite(n) && String(n) === key;
}

/**
 * Mid-turn journal POST: keep the outbox map's numeric keys as explicit ``seq``.
 * ``ord`` is a disk-only twin of Postgres ``created_at`` — strip it from the entry.
 */
export function journalEntriesWithExplicitSeq(
  journal: Record<string, unknown> | undefined,
): Array<{ seq: number; entry: unknown }> {
  if (!journal || typeof journal !== "object") return [];
  const rows: Array<{ seq: number; entry: unknown }> = [];
  for (const key of Object.keys(journal)) {
    if (!isNumericJournalKey(key)) continue;
    const seq = Number(key);
    if (!Number.isFinite(seq)) continue;
    rows.push({ seq, entry: stripEntryOrd(journal[key]) });
  }
  rows.sort((a, b) => a.seq - b.seq);
  return rows;
}

/** ``upsert_stream_segments`` wire: channel / text / generation. */
export function streamSegmentsForPost(
  segs: OutboxRecord["stream_segments"],
): Array<{ channel: string; text: string; generation: number }> {
  if (!segs || typeof segs !== "object") return [];
  const rows: Array<{ channel: string; text: string; generation: number }> = [];
  for (const [channel, raw] of Object.entries(segs)) {
    if (!raw || typeof raw !== "object") continue;
    const text = String(raw.text || "");
    const generation =
      typeof raw.generation === "number" && Number.isFinite(raw.generation)
        ? raw.generation
        : 0;
    rows.push({ channel, text, generation });
  }
  return rows;
}

export function journalEntriesFromMap(
  journal: Record<string, unknown> | undefined,
): unknown[] | undefined {
  if (!journal || typeof journal !== "object") return undefined;
  const keys = Object.keys(journal);
  if (keys.length === 0) return undefined;

  const numericKeys = keys.filter(isNumericJournalKey);
  const numericOrds = numericKeys.map((k) => entryOrd(journal[k]));
  if (
    numericKeys.length > 0 &&
    numericOrds.every((item): item is number => item !== null)
  ) {
    const ordered = [...numericKeys].sort((a, b) => {
      const d =
        (entryOrd(journal[a]) as number) - (entryOrd(journal[b]) as number);
      if (d !== 0) return d;
      return Number(a) - Number(b);
    });
    const other = keys.filter((k) => !isNumericJournalKey(k));
    return [...ordered, ...other].map((k) => stripEntryOrd(journal[k]));
  }

  // Legacy files without ``ord``: integer-key order (JS already lost insertion
  // order at JSON.parse). Same behaviour the previous `.sort()` pinned.
  const sorted = [...keys].sort((a, b) => {
    const na = Number(a);
    const nb = Number(b);
    if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
    return a.localeCompare(b);
  });
  return sorted.map((k) => journal[k]);
}

/** Mirror server ``JOURNAL_OVERFLOW_SEQ_START`` (pause-turn overflow band). */
export const JOURNAL_OVERFLOW_SEQ_START = 1_000_000;

/**
 * Mid-turn projection: only seqs the cloud has not confirmed.
 * Live and overflow bands are independent watermarks (overflow keys sit at
 * ``JOURNAL_OVERFLOW_SEQ_START+``; a single max seq would skip later live keys).
 */
export function unackedJournalEntries(
  entries: Array<{ seq: number; entry: unknown }>,
  ackedLiveSeq: number,
  ackedOverflowSeq: number,
): Array<{ seq: number; entry: unknown }> {
  return entries.filter(({ seq }) =>
    seq >= JOURNAL_OVERFLOW_SEQ_START
      ? seq > ackedOverflowSeq
      : seq > ackedLiveSeq,
  );
}

export function journalAckAfterPost(
  posted: ReadonlyArray<{ seq: number }>,
  ackedLiveSeq: number,
  ackedOverflowSeq: number,
): { ackedLiveSeq: number; ackedOverflowSeq: number } {
  let live = ackedLiveSeq;
  let overflow = ackedOverflowSeq;
  for (const { seq } of posted) {
    if (seq >= JOURNAL_OVERFLOW_SEQ_START) overflow = Math.max(overflow, seq);
    else live = Math.max(live, seq);
  }
  return { ackedLiveSeq: live, ackedOverflowSeq: overflow };
}

function journalMapAfterSeq(
  journal: Record<string, unknown>,
  afterSeq: number,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(journal)) {
    if (!isNumericJournalKey(key)) continue;
    const seq = Number(key);
    if (seq >= JOURNAL_OVERFLOW_SEQ_START) continue;
    if (seq > afterSeq) out[key] = value;
  }
  return out;
}

const TOOL_FAILURE_MESSAGE_MAX = 200;

/** Known local-turn write-back failure codes (mirrors server frozenset). */
const LOCAL_TURN_TOOL_FAILURE_CODES = new Set([
  "searxng_unreachable",
  "egress_connect",
  "declaration_empty",
  "declaration_xor",
  "declaration_unknown",
  "exec_timeout",
  "exec_forced_stop",
  "schema",
  "git_timeout",
  "no_repo",
  "dirty_skip",
  "already_repo",
  "no_remote",
  "not_github",
  "unauthenticated",
  "invalid_args",
  "network_error",
  "auth_failed",
  "api_error",
  "not_found",
  "validation_failed",
  "no_default_branch",
  "not_a_web_url",
  "url_not_workspace_path",
  "project_verify_redirect",
  "source_grep_redirect",
  "source_dump_redirect",
  "long_running_redirect",
  "loopback_host",
  "access_denied",
  "outside_workspace",
  "other",
  "too_large",
]);

const CHANNEL_REDIRECT_CODES = new Set([
  "source_grep_redirect",
  "source_dump_redirect",
  "project_verify_redirect",
  "long_running_redirect",
  "not_a_web_url",
  "url_not_workspace_path",
  "loopback_host",
]);

function remapPathOrVerifyFailure(raw: string): string | null {
  if (raw.includes("跑项目级慢验证")) {
    return "project_verify_redirect";
  }
  if (raw.includes("打开源码再正则扫描")) {
    return "source_grep_redirect";
  }
  if (raw.includes("把工作区文件 dump 到 stdout")) {
    return "source_dump_redirect";
  }
  if (
    raw.includes("禁止用 code_execute 启动长驻进程") ||
    raw.includes("请用 run 启动长驻进程")
  ) {
    return "long_running_redirect";
  }
  if (
    [
      "文件不存在：",
      "片段文件不存在：",
      "源路径不存在：",
      "不是目录：",
      "路径不存在：",
      "区外路径不存在",
    ].some((n) => raw.includes(n))
  ) {
    return "not_found";
  }
  const lowered = raw.toLowerCase();
  if (
    [
      "winerror 5",
      "winerror 32",
      "access is denied",
      "access denied",
      "sharing violation",
      "拒绝访问",
    ].some((n) => lowered.includes(n)) ||
    raw.includes("写入被占用")
  ) {
    return "access_denied";
  }
  if (raw.includes("超出了工作区范围")) {
    return "outside_workspace";
  }
  return null;
}

/**
 * Coarse write-back failure codes (mirrors server
 * ``normalize_local_turn_tool_failure_code``).
 * Declaration gate: structured reject templates only (not free-text intent).
 */
export function normalizeToolFailureCode(
  message: string,
  code?: string | null,
): string {
  const rawCode = (code || "").trim();
  if (LOCAL_TURN_TOOL_FAILURE_CODES.has(rawCode)) {
    if (rawCode === "schema" || rawCode === "other") {
      const remapped = remapPathOrVerifyFailure(message || "");
      if (remapped) return remapped;
    }
    return rawCode;
  }
  // Legacy git wall-clock used bare ``timeout``; fact write now emits ``git_timeout``.
  if (rawCode === "timeout") {
    return "git_timeout";
  }
  if (rawCode === "verify_budget" || rawCode === "exec_env_timeout") {
    return "exec_timeout";
  }
  if (rawCode === "exec_forced_stop") {
    return "exec_forced_stop";
  }
  const raw = message || "";
  // Mirror server try_declaration_reject_gate prefixes / templates.
  if (
    raw.startsWith("playbook 与 tasks 二选一") ||
    raw.startsWith("手写 tasks 时勿传")
  ) {
    return "declaration_xor";
  }
  if (
    raw.startsWith("delegate 须传手写") ||
    raw.startsWith("delegate 缺 tasks/playbook")
  ) {
    return "declaration_empty";
  }
  if (raw.startsWith("未知 playbook")) {
    return "declaration_unknown";
  }
  if (
    raw.includes("ExecEnvProbeFailed:") ||
    raw.includes("Timeout: no output for") ||
    raw.includes("Timeout: forced stop after") ||
    raw.includes("Timeout: execution exceeded") ||
    (raw.includes("验证未在") && raw.includes("预算内完成"))
  ) {
    return raw.includes("forced stop after")
      ? "exec_forced_stop"
      : "exec_timeout";
  }
  if (raw.includes("缺少必填参数")) {
    return "schema";
  }
  const text = raw.toLowerCase();
  if (
    text.includes("searxng") ||
    raw.includes("搜索服务") ||
    (text.includes("unreachable") && text.includes("searx"))
  ) {
    return "searxng_unreachable";
  }
  if (
    [
      "connecterror",
      "connect timeout",
      "connecttimeout",
      "egress",
      "network is unreachable",
    ].some((n) => text.includes(n)) ||
    ["无法建立连接", "出网受限", "连接超时", "连接失败"].some((n) =>
      raw.includes(n),
    )
  ) {
    return "egress_connect";
  }
  const remapped = remapPathOrVerifyFailure(raw);
  if (remapped) return remapped;
  return "other";
}

function truncateToolFailureMessage(message: string): string {
  if (!message) return "";
  return message.length <= TOOL_FAILURE_MESSAGE_MAX
    ? message
    : message.slice(0, TOOL_FAILURE_MESSAGE_MAX);
}

/**
 * Project failed tool facts into ``RecordTurnRequest.tool_failures``.
 * Prefers journal ``tool_call`` success=false; falls back to ``tool_use_end`` status=error.
 */
export function toolFailuresFromJournal(
  entries: unknown[] | undefined,
): Array<{ tool: string; code: string; message: string }> {
  if (!entries || entries.length === 0) return [];

  const row = (
    tool: string,
    message: string,
    code?: string | null,
  ): { tool: string; code: string; message: string } | null => {
    const name = (tool || "").trim();
    if (!name) return null;
    const msg = truncateToolFailureMessage(message);
    return {
      tool: name.slice(0, 128),
      code: normalizeToolFailureCode(msg, code),
      message: msg,
    };
  };

  const fromFacts: Array<{ tool: string; code: string; message: string }> = [];
  for (const entry of entries) {
    if (!entry || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    if (e.kind !== "tool_call") continue;
    const payload =
      e.payload && typeof e.payload === "object"
        ? (e.payload as Record<string, unknown>)
        : null;
    if (!payload || payload.success !== false) continue;
    const rawCode =
      typeof payload.code === "string" ? payload.code.trim() : null;
    const built = row(
      String(payload.name || ""),
      String(payload.result || ""),
      rawCode || null,
    );
    if (built) fromFacts.push(built);
  }
  if (fromFacts.length > 0) {
    return fromFacts.filter((r) => !CHANNEL_REDIRECT_CODES.has(r.code));
  }

  const fromEnds: Array<{ tool: string; code: string; message: string }> = [];
  for (const entry of entries) {
    if (!entry || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    if (e.kind !== "tool_use_end") continue;
    const payload =
      e.payload && typeof e.payload === "object"
        ? (e.payload as Record<string, unknown>)
        : null;
    if (!payload || (payload.status ?? "success") === "success") continue;
    if (payload.status === "redirect") continue;
    const failure =
      payload.failure && typeof payload.failure === "object"
        ? (payload.failure as Record<string, unknown>)
        : null;
    const nestedCode =
      typeof failure?.code === "string" ? failure.code.trim() : null;
    const rawCode =
      typeof payload.code === "string" ? payload.code.trim() : nestedCode;
    const built = row(
      String(payload.tool_name || ""),
      String(payload.result || ""),
      rawCode || null,
    );
    if (built) fromEnds.push(built);
  }
  return fromEnds.filter((r) => !CHANNEL_REDIRECT_CODES.has(r.code));
}

/**
 * Legacy wire marker when older clients filled empty user_message to satisfy
 * ``RecordTurnRequest.user_message`` min_length=1. Server treats this (and empty
 * um) as no real user intent and will not insert a visible user row. New drains
 * POST empty string instead — do not write this into outbox / history.
 */
export const EMPTY_USER_MESSAGE_PLACEHOLDER = "[local-turn recovery]";

/**
 * Ready rows with empty user_message may still POST when they carry process
 * projection (journal / runs / stream segments) and a safe umid. Typical stock
 * dead-letter also has message_id; umid alone is enough once C1 restores identity.
 */
export function canPostEmptyUserMessage(record: OutboxRecord): boolean {
  if (!isSafeOutboxId(record.user_message_id)) return false;
  return recordHasProcessState(record);
}

/**
 * Gate for empty-um writeback when {@link canPostEmptyUserMessage}.
 * Does **not** fill a visible placeholder (ffafc42b) — leaves user_message empty.
 * Returns true when the record is postable (non-empty um, or empty+process).
 */
export function fillEmptyUserMessageForWriteback(
  record: OutboxRecord,
): boolean {
  const um = (record.user_message || "").trim();
  if (um && um !== EMPTY_USER_MESSAGE_PLACEHOLDER) return true;
  // Normalize legacy placeholder off the record so POST / disk stay empty.
  if (um === EMPTY_USER_MESSAGE_PLACEHOLDER) {
    record.user_message = "";
  }
  return canPostEmptyUserMessage(record);
}

/**
 * When hard-kill left content empty, promote captain stream snapshots into
 * content / reasoning_content (incomplete salvage). Returns true when the
 * record has any salvageable body after this fill.
 */
export function fillFromCaptainStreamSegments(record: OutboxRecord): boolean {
  const segs = record.stream_segments;
  if (!segs || typeof segs !== "object") {
    return (
      !!(record.content || "").trim() ||
      !!(record.reasoning_content || "").trim()
    );
  }
  const contentSeg = segs[CHANNEL_CAPTAIN_CONTENT];
  const contentText =
    contentSeg && typeof contentSeg === "object"
      ? String(contentSeg.text || "")
      : "";
  if (!(record.content || "").trim() && contentText.trim()) {
    record.content = contentText;
  }
  const reasoningSeg = segs[CHANNEL_CAPTAIN_REASONING];
  const reasoningText =
    reasoningSeg && typeof reasoningSeg === "object"
      ? String(reasoningSeg.text || "")
      : "";
  if (!(record.reasoning_content || "").trim() && reasoningText.trim()) {
    record.reasoning_content = reasoningText;
  }
  return (
    !!(record.content || "").trim() || !!(record.reasoning_content || "").trim()
  );
}

/** True when the turn carried process projection (must not treat null assistant as success). */
export function recordHasProcessState(record: OutboxRecord): boolean {
  const runs = record.runs;
  if (runs && typeof runs === "object" && Object.keys(runs).length > 0) {
    return true;
  }
  const journal = record.journal;
  if (
    journal &&
    typeof journal === "object" &&
    Object.keys(journal).length > 0
  ) {
    return true;
  }
  const segs = record.stream_segments;
  if (segs && typeof segs === "object") {
    for (const entry of Object.values(segs)) {
      if (
        entry &&
        typeof entry === "object" &&
        String(entry.text || "").trim()
      ) {
        return true;
      }
    }
  }
  return !!(record.reasoning_content || "").trim();
}

/**
 * Whether an OPEN row should be salvaged (promote / writeback) instead of
 * aborting the cloud running placeholder — same predicate as salvageOpen in drain.
 */
export function shouldSalvageOpenRecord(
  record: OutboxRecord | null | undefined,
): boolean {
  if (!record || record.phase !== PHASE_OPEN) return false;
  const hasText = fillFromCaptainStreamSegments(record);
  if (hasText || recordHasProcessState(record)) return true;
  const um = (record.user_message || "").trim();
  const hasUm = !!um && um !== EMPTY_USER_MESSAGE_PLACEHOLDER;
  const hasTrace = (record.trace_id || "").trim().length === 32;
  return hasUm && hasTrace;
}

/**
 * Delete outbox only when assistant truly landed, or server explicitly marked noop.
 * HTTP 200 + `assistant_message_id==null` with process state is a false ack.
 */
export function shouldDeleteOutboxAfterAck(
  body: {
    assistant_message_id?: string | null;
    noop?: boolean | null;
  },
  record: OutboxRecord,
): boolean {
  if (body.assistant_message_id) return true;
  if (body.noop === true) return true;
  // Legacy servers omit `noop`: empty true-no-op (no process) may still delete.
  if (!recordHasProcessState(record)) return true;
  return false;
}

export function toRecordTurnBody(
  record: OutboxRecord,
): Record<string, unknown> {
  const rawUm = record.user_message || "";
  const userMessage =
    rawUm.trim() === EMPTY_USER_MESSAGE_PLACEHOLDER ? "" : rawUm;
  const body: Record<string, unknown> = {
    user_message: userMessage,
    user_message_id: record.user_message_id,
    content: record.content || "",
    reasoning_content: record.reasoning_content ?? null,
    citations: record.citations || [],
    runs: record.runs ?? null,
    message_id: record.message_id ?? null,
    input_tokens: record.input_tokens ?? 0,
    output_tokens: record.output_tokens ?? 0,
    reasoning_tokens: record.reasoning_tokens ?? 0,
    cache_hit_tokens: record.cache_hit_tokens ?? 0,
    cache_miss_tokens: record.cache_miss_tokens ?? 0,
    rounds: record.rounds ?? 0,
    trace_id: record.trace_id || "",
    finish_reason: record.finish_reason ?? null,
  };
  const journal = journalEntriesFromMap(record.journal);
  if (journal) body.journal = journal;
  const watermark = record.resume_after_seq;
  const failureJournal =
    typeof watermark === "number" &&
    Number.isFinite(watermark) &&
    record.journal
      ? journalEntriesFromMap(journalMapAfterSeq(record.journal, watermark))
      : journal;
  const failures = toolFailuresFromJournal(failureJournal);
  if (failures.length > 0) body.tool_failures = failures;
  const origin = (record.origin || "").trim();
  if (origin) body.origin = origin;
  const executionId = (record.execution_id || "").trim();
  if (executionId) body.execution_id = executionId;
  const harvestKind = (record.harvest_kind || "").trim();
  if (harvestKind) body.harvest_kind = harvestKind;
  const mentions = Array.isArray(record.agent_mentions)
    ? record.agent_mentions.filter(
        (m) =>
          m &&
          typeof m.agent_id === "string" &&
          m.agent_id.trim() &&
          typeof m.role === "string" &&
          m.role.trim(),
      )
    : [];
  if (mentions.length > 0) body.agent_mentions = mentions;
  return body;
}

export async function readOutboxRecord(
  userMessageId: string,
): Promise<OutboxRecord | null> {
  if (!isSafeOutboxId(userMessageId)) return null;
  try {
    const raw = await readFile(
      join(outboxDir(), `${userMessageId}.json`),
      "utf-8",
    );
    const data = JSON.parse(raw) as OutboxRecord;
    if (
      !data?.user_message_id ||
      !data.conversation_id ||
      !isSafeOutboxId(data.user_message_id) ||
      !isSafeOutboxId(data.conversation_id)
    ) {
      return null;
    }
    return data;
  } catch {
    return null;
  }
}

export async function readOutboxRecords(): Promise<OutboxRecord[]> {
  const dir = outboxDir();
  let names: string[];
  try {
    names = await readdir(dir);
  } catch {
    return [];
  }
  const records: OutboxRecord[] = [];
  for (const name of names) {
    // Final records are `*.json`; unique temps are `*.json.<pid>-<seq>.tmp`.
    if (!name.endsWith(".json") || name.includes(".tmp")) continue;
    try {
      const raw = await readFile(join(dir, name), "utf-8");
      const data = JSON.parse(raw) as OutboxRecord;
      if (
        data?.user_message_id &&
        data.conversation_id &&
        isSafeOutboxId(data.user_message_id) &&
        isSafeOutboxId(data.conversation_id)
      ) {
        records.push(data);
      }
    } catch {
      // torn / unreadable — skip
    }
  }
  return records;
}

export async function writeRecord(record: OutboxRecord): Promise<void> {
  if (!isSafeOutboxId(record.user_message_id)) {
    throw new Error("unsafe outbox user_message_id");
  }
  const dir = outboxDir();
  await mkdir(dir, { recursive: true });
  const target = join(dir, `${record.user_message_id}.json`);
  const tmp = tmpPathFor(target);
  try {
    await writeFile(tmp, JSON.stringify(record), "utf-8");
    await rename(tmp, target);
  } catch (err) {
    await unlink(tmp).catch(() => undefined);
    throw err;
  }
}

export async function deleteRecord(userMessageId: string): Promise<void> {
  if (!isSafeOutboxId(userMessageId)) return;
  try {
    await unlink(join(outboxDir(), `${userMessageId}.json`));
  } catch {
    /* already gone */
  }
}
