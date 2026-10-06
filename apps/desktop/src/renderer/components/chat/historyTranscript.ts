/** Display mirror of prior-turn messages. Wire format is owned by
 * `_format_captain_history`: `@@{role} {length} {name}?\\n` plus exactly
 * `length` Unicode code points of the raw message (Python `len`), then a
 * newline. The model still receives the original messages; this string is
 * only the reader mirror.
 */

export type HistoryRole = "user" | "assistant" | "tool" | "system" | "other";

export interface HistoryRecord {
  role: HistoryRole;
  name: string;
  text: string;
}

const HEADER = /^@@(user|assistant|tool|system|other) (\d+)(?: (\S+))?$/;

/** `length` is a code-point count. `slice` counts UTF-16 units, so one emoji
 * would leave the following character (often 「。」) as a bogus `other` turn. */
function takeCodePoints(
  text: string,
  count: number,
): { taken: string; rest: string } | null {
  let units = 0;
  for (let seen = 0; seen < count; seen += 1) {
    if (units >= text.length) return null;
    const code = text.codePointAt(units);
    if (code === undefined) return null;
    units += code > 0xffff ? 2 : 1;
  }
  return { taken: text.slice(0, units), rest: text.slice(units) };
}

/** `null` means the body is the older prose mirror (or empty). */
export function parseHistoryTranscript(body: string): HistoryRecord[] | null {
  if (!body.startsWith("@@")) return null;
  const records: HistoryRecord[] = [];
  let rest = body;
  while (rest.length > 0) {
    const nl = rest.indexOf("\n");
    if (nl < 0) {
      records.push({ role: "other", name: "", text: rest });
      break;
    }
    const match = HEADER.exec(rest.slice(0, nl));
    if (!match) {
      records.push({ role: "other", name: "", text: rest });
      break;
    }
    const length = Number(match[2]);
    const payload = rest.slice(nl + 1);
    if (!Number.isInteger(length) || length < 0) {
      records.push({ role: "other", name: "", text: payload });
      break;
    }
    const sliced = takeCodePoints(payload, length);
    if (sliced == null) {
      records.push({ role: "other", name: "", text: payload });
      break;
    }
    records.push({
      role: match[1] as HistoryRole,
      name: match[3] ?? "",
      text: sliced.taken,
    });
    rest = sliced.rest;
    if (rest.startsWith("\n")) rest = rest.slice(1);
    else if (rest.length > 0) {
      records.push({ role: "other", name: "", text: rest });
      break;
    }
  }
  return records;
}

export type ToolFace =
  | { kind: "page"; title: string; url: string; body: string; note: string }
  | { kind: "code"; caption: string; code: string }
  | { kind: "text"; text: string };

function parseJson(text: string): unknown | undefined {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function pageOf(value: unknown): ToolFace | null {
  const record = asRecord(value);
  if (!record || typeof record.content !== "string") return null;
  return {
    kind: "page",
    title: typeof record.title === "string" ? record.title : "",
    url: typeof record.url === "string" ? record.url : "",
    body: record.content,
    note: typeof record.note === "string" ? record.note : "",
  };
}

/** Page JSON shows decoded prose. Other JSON (folder roster) stays a code block.
 * A one-line preface plus JSON keeps the preface and pretty-prints the object.
 */
export function presentToolReceipt(text: string): ToolFace {
  const direct = parseJson(text.trim());
  const page = pageOf(direct);
  if (page) return page;
  if (direct !== undefined && (Array.isArray(direct) || asRecord(direct))) {
    return { kind: "code", caption: "", code: JSON.stringify(direct, null, 2) };
  }
  const split = text.indexOf("\n");
  if (split > 0) {
    const tail = parseJson(text.slice(split + 1).trim());
    if (
      tail !== undefined &&
      pageOf(tail) == null &&
      (Array.isArray(tail) || asRecord(tail))
    ) {
      return {
        kind: "code",
        caption: text.slice(0, split).trim(),
        code: JSON.stringify(tail, null, 2),
      };
    }
  }
  return { kind: "text", text };
}
