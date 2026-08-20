/**
 * Strip desktop.jsonl down to connectivity / recovery diagnostics.
 *
 * The write path already forbids tokens / passwords / message bodies in
 * ``fields``, but a support pack leaves the user's machine — allowlist events
 * and primitive fields so a leaked content/token key cannot ride along.
 */

/** Events that explain disconnect / rejoin. Everything else is dropped. */
const EVENT_ALLOW_PREFIXES = [
  "server_health.",
  "sse.",
  "conversation.follow_",
  "conversation.rejoin_",
] as const;

const EVENT_ALLOW_EXACT = new Set(["sidecar.turn_already_running"]);

const FIELD_ALLOW = new Set([
  "timestamp",
  "level",
  "event",
  "build",
  "version",
  "conversation_id",
  "source",
  "reason",
  "last_ok_at",
  "from",
  "consecutive_failures",
  "since_offline_ms",
  "status",
  "failure_threshold",
  "saw_any_event",
  "op",
  "turn_id",
  "request_id",
  "attempts",
  "attempt",
  "outcome",
  "delay_ms",
  "duration_ms",
  "idle_timeout_ms",
  "event_type",
  "turn_phase",
]);

const SECRET_KEY =
  /token|password|secret|authorization|cookie|content|body|text|path|filename|message/i;

const JWT_LIKE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

/** Envelope copied onto every jsonl row — say once on the 排查包 section. */
const ENVELOPE_KEYS = ["build", "version", "conversation_id"] as const;

/** Fold metadata — ignored when matching consecutive duplicates. */
const FOLD_IGNORE_KEYS = new Set(["timestamp", "count", "first", "last"]);

/**
 * Soft cap after the 64KB file tail. Identical events (same fields except
 * timestamp) roll up first, so a reconnect storm is one counted line instead
 * of filling the window. Ambient ``server_health.*`` is still never dropped
 * to make room for scoped events (see {@link trimDesktopLogExcerpt}).
 */
export const DESKTOP_LOG_EXCERPT_MAX_EVENTS = 400;

export type DesktopLogPrimitive = string | number | boolean | null;
export type SanitizedDesktopLogRecord = Record<string, DesktopLogPrimitive>;

export type DesktopLogExcerptHeader = {
  build?: string;
  version?: string;
  conversation_id?: string;
  level?: string;
};

export function isAllowedDesktopLogEvent(event: string): boolean {
  if (EVENT_ALLOW_EXACT.has(event)) return true;
  return EVENT_ALLOW_PREFIXES.some((prefix) => event.startsWith(prefix));
}

/** App-wide connectivity — ``server_health.*`` never carries conversation_id. */
export function isAmbientDesktopLogEvent(event: string): boolean {
  return event.startsWith("server_health.");
}

/**
 * Keep a sanitized record in a conversation's 排查包?
 *
 * ``server_health.*`` and any allowlisted line with no ``conversation_id``
 * are session-unrelated diagnostics (offline banner / event-loop / network)
 * and must stay. Only drop lines that name a *different* conversation.
 */
export function isRelevantDesktopLogRecord(
  record: {
    event?: unknown;
    conversation_id?: unknown;
    [key: string]: unknown;
  },
  conversationId?: string | null,
): boolean {
  const event = typeof record.event === "string" ? record.event : "";
  if (event && isAmbientDesktopLogEvent(event)) return true;
  const cid = record.conversation_id;
  if (typeof cid !== "string" || !cid) return true;
  const want = conversationId?.trim() || "";
  return !want || cid === want;
}

function isAmbientRecord(record: SanitizedDesktopLogRecord): boolean {
  const event = record.event;
  return (
    (typeof event === "string" && isAmbientDesktopLogEvent(event)) ||
    typeof record.conversation_id !== "string" ||
    !record.conversation_id
  );
}

function trimDesktopLogExcerpt(
  rows: Array<{ line: string; ambient: boolean }>,
  maxEvents: number,
): string[] {
  if (rows.length <= maxEvents) return rows.map((r) => r.line);
  const ambientCount = rows.reduce((n, r) => n + (r.ambient ? 1 : 0), 0);
  const scopedBudget = Math.max(
    0,
    maxEvents - Math.min(ambientCount, maxEvents),
  );
  const picked: Array<{ line: string; ambient: boolean }> = [];
  let scopedKept = 0;
  for (let i = rows.length - 1; i >= 0; i--) {
    const row = rows[i];
    if (row.ambient) {
      picked.push(row);
    } else if (scopedKept < scopedBudget) {
      picked.push(row);
      scopedKept += 1;
    }
  }
  picked.reverse();
  return picked.length > maxEvents
    ? picked.slice(-maxEvents).map((r) => r.line)
    : picked.map((r) => r.line);
}

function looksSecret(value: string): boolean {
  if (value.startsWith("sk-") || /^Bearer\s+/i.test(value)) return true;
  if (value.length > 20 && JWT_LIKE.test(value)) return true;
  return false;
}

function isAllowedPrimitive(
  value: unknown,
): value is string | number | boolean | null {
  if (value === null) return true;
  if (typeof value === "number" || typeof value === "boolean") return true;
  if (typeof value !== "string") return false;
  return !looksSecret(value);
}

function pickAllowedFields(
  record: Record<string, unknown>,
): SanitizedDesktopLogRecord {
  const out: SanitizedDesktopLogRecord = {};
  for (const [key, value] of Object.entries(record)) {
    if (!FIELD_ALLOW.has(key)) continue;
    if (SECRET_KEY.test(key) && key !== "reason") continue;
    if (!isAllowedPrimitive(value)) continue;
    out[key] = value;
  }
  return out;
}

function collapseKey(record: SanitizedDesktopLogRecord): string {
  const pairs: Array<[string, DesktopLogPrimitive]> = [];
  for (const key of Object.keys(record).sort()) {
    if (FOLD_IGNORE_KEYS.has(key)) continue;
    const value = record[key];
    if (value === undefined) continue;
    pairs.push([key, value]);
  }
  return JSON.stringify(pairs);
}

function occurrenceCount(record: SanitizedDesktopLogRecord): number {
  return typeof record.count === "number" && record.count >= 1
    ? record.count
    : 1;
}

function firstTimestamp(record: SanitizedDesktopLogRecord): string | undefined {
  if (typeof record.first === "string" && record.first) return record.first;
  if (typeof record.timestamp === "string" && record.timestamp) {
    return record.timestamp;
  }
  return undefined;
}

function lastTimestamp(record: SanitizedDesktopLogRecord): string | undefined {
  if (typeof record.last === "string" && record.last) return record.last;
  if (typeof record.timestamp === "string" && record.timestamp) {
    return record.timestamp;
  }
  return undefined;
}

function stripFoldMeta(
  record: SanitizedDesktopLogRecord,
): SanitizedDesktopLogRecord {
  const out: SanitizedDesktopLogRecord = {};
  for (const [key, value] of Object.entries(record)) {
    if (FOLD_IGNORE_KEYS.has(key)) continue;
    out[key] = value;
  }
  return out;
}

function collapsedRecord(
  base: SanitizedDesktopLogRecord,
  count: number,
  first: string | undefined,
  last: string | undefined,
): SanitizedDesktopLogRecord {
  const out = stripFoldMeta(base);
  out.count = count;
  if (first) out.first = first;
  if (last) out.last = last;
  return out;
}

/** warn / error anchor the timeline — they stay verbatim, in place, unrolled. */
function isFoldableRecord(record: SanitizedDesktopLogRecord): boolean {
  return record.level !== "warn" && record.level !== "error";
}

/**
 * Roll rows that share an event and every field except timestamp up into their
 * first occurrence, carrying ``count`` / ``first`` / ``last``.
 *
 * Adjacency is deliberately not required: yielding the stream to a local turn
 * alternates ``follow_closed`` / ``follow_open``, so folding only neighbours
 * would leave every pair intact and shrink nothing.
 */
export function foldDesktopLogRecords(
  records: readonly SanitizedDesktopLogRecord[],
): SanitizedDesktopLogRecord[] {
  const out: SanitizedDesktopLogRecord[] = [];
  const anchorAt = new Map<string, number>();
  for (const rec of records) {
    if (!isFoldableRecord(rec)) {
      out.push({ ...rec });
      continue;
    }
    const key = collapseKey(rec);
    const at = anchorAt.get(key);
    if (at === undefined) {
      anchorAt.set(key, out.length);
      out.push({ ...rec });
      continue;
    }
    const prev = out[at];
    if (prev === undefined) continue;
    out[at] = collapsedRecord(
      prev,
      occurrenceCount(prev) + occurrenceCount(rec),
      firstTimestamp(prev) ?? firstTimestamp(rec),
      lastTimestamp(rec) ?? lastTimestamp(prev),
    );
  }
  return out;
}

/**
 * Lift repeated envelope fields onto a section header. ``level: info`` is the
 * omitted default; warn / error stay on the line so they remain scannable.
 *
 * Hoisting ``conversation_id`` would otherwise make an ambient row (no
 * conversation of its own) read as if it belonged to the header's conversation,
 * so those rows get an explicit ``scope: "app"`` instead.
 */
export function hoistDesktopLogEnvelope(
  records: readonly SanitizedDesktopLogRecord[],
): {
  header: DesktopLogExcerptHeader;
  records: SanitizedDesktopLogRecord[];
} {
  const header: DesktopLogExcerptHeader = {};
  for (const key of ENVELOPE_KEYS) {
    const values = new Set<string>();
    for (const rec of records) {
      const value = rec[key];
      if (typeof value === "string" && value) values.add(value);
    }
    if (values.size === 1) header[key] = [...values][0];
  }

  let strippedInfo = false;
  const cidHoisted = header.conversation_id !== undefined;
  const next = records.map((rec) => {
    const out: SanitizedDesktopLogRecord = { ...rec };
    for (const key of ENVELOPE_KEYS) {
      const hoisted = header[key];
      if (hoisted !== undefined && out[key] === hoisted) delete out[key];
    }
    const scoped =
      typeof rec.conversation_id === "string" && rec.conversation_id !== "";
    if (cidHoisted && !scoped) out.scope = "app";
    if (out.level === "info") {
      strippedInfo = true;
      const { level: _info, ...rest } = out;
      return rest;
    }
    return out;
  });
  if (strippedInfo) header.level = "info";
  return { header, records: next };
}

export function formatDesktopLogExcerptHeader(
  header: DesktopLogExcerptHeader,
): string[] {
  const lines: string[] = [];
  for (const key of ["build", "version", "conversation_id", "level"] as const) {
    const value = header[key];
    if (value) lines.push(`${key}: ${value}`);
  }
  return lines;
}

/**
 * One JSONL line → allowlisted record, or ``null`` if it is not diagnostic.
 */
export function sanitizeDesktopLogRecord(
  raw: unknown,
): SanitizedDesktopLogRecord | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const rec = raw as Record<string, unknown>;
  const event = typeof rec.event === "string" ? rec.event : "";
  if (!event || !isAllowedDesktopLogEvent(event)) return null;

  const fields =
    rec.fields && typeof rec.fields === "object" && !Array.isArray(rec.fields)
      ? (rec.fields as Record<string, unknown>)
      : {};

  const picked = pickAllowedFields({ ...fields, ...rec, event });
  if (typeof picked.event !== "string") return null;
  return picked;
}

/** Drop a leading partial line when the tail started mid-record. */
export function dropPartialJsonlPrefix(text: string): string {
  if (!text) return "";
  if (text.startsWith("{")) return text;
  const nl = text.indexOf("\n");
  return nl < 0 ? "" : text.slice(nl + 1);
}

export function sanitizeDesktopLogLines(
  text: string,
  opts?: { conversationId?: string | null; maxEvents?: number },
): string[] {
  const conversationId = opts?.conversationId?.trim() || "";
  const maxEvents = opts?.maxEvents ?? DESKTOP_LOG_EXCERPT_MAX_EVENTS;
  const lines = dropPartialJsonlPrefix(text).split("\n");
  const kept: SanitizedDesktopLogRecord[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      continue;
    }
    const sanitized = sanitizeDesktopLogRecord(parsed);
    if (!sanitized) continue;
    if (!isRelevantDesktopLogRecord(sanitized, conversationId)) continue;
    kept.push(sanitized);
  }
  const folded = foldDesktopLogRecords(kept);
  return trimDesktopLogExcerpt(
    folded.map((record) => ({
      line: JSON.stringify(record),
      ambient: isAmbientRecord(record),
    })),
    maxEvents,
  );
}
