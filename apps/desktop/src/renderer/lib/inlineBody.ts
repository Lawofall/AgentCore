/**
 * Inline attachment / mention markers in user ``content``.
 *
 * Composer pills persist as U+FFFC-delimited tokens so body order is one sequence.
 * Indices address attachments[] / agentMentions[] in appearance order.
 * Token: U+FFFC + A|M + decimal index + U+FFFC. Must match
 * ``agentcore.conversation.inline_body``.
 */

export const INLINE_OBJECT = "\uFFFC";

const TOKEN_RE = /\uFFFC([AM])(\d+)\uFFFC/g;

export type InlineSpan =
  | { kind: "text"; text: string }
  | { kind: "attachment"; index: number }
  | { kind: "mention"; index: number };

export function inlineToken(kind: "A" | "M", index: number): string {
  return `${INLINE_OBJECT}${kind}${index}${INLINE_OBJECT}`;
}

const TOKEN_TEST = /\uFFFC[AM]\d+\uFFFC/;

export function hasInlineMarkers(content: string | null | undefined): boolean {
  if (!content) return false;
  return TOKEN_TEST.test(content);
}

export function parseInlineBody(content: string): InlineSpan[] {
  if (!content) return [];
  const spans: InlineSpan[] = [];
  let pos = 0;
  TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null = TOKEN_RE.exec(content);
  while (m) {
    if (m.index > pos) {
      spans.push({ kind: "text", text: content.slice(pos, m.index) });
    }
    const index = Number(m[2]);
    spans.push(
      m[1] === "A" ? { kind: "attachment", index } : { kind: "mention", index },
    );
    pos = m.index + m[0].length;
    m = TOKEN_RE.exec(content);
  }
  if (pos < content.length) {
    spans.push({ kind: "text", text: content.slice(pos) });
  }
  return spans;
}

export function serializeInlineBody(spans: readonly InlineSpan[]): string {
  let out = "";
  for (const span of spans) {
    if (span.kind === "text") {
      out += span.text.replaceAll(INLINE_OBJECT, "");
    } else if (span.kind === "attachment") {
      out += inlineToken("A", span.index);
    } else {
      out += inlineToken("M", span.index);
    }
  }
  return out;
}

export function plainText(content: string | null | undefined): string {
  if (!content) return "";
  return content.replace(TOKEN_RE, "").replaceAll(INLINE_OBJECT, "");
}

export function migrateLegacyDraft(
  content: string,
  attachmentCount: number,
  mentionCount: number,
): string {
  if (hasInlineMarkers(content)) return content;
  if (attachmentCount <= 0 && mentionCount <= 0) return content;
  let extra = "";
  for (let i = 0; i < attachmentCount; i++) extra += inlineToken("A", i);
  for (let i = 0; i < mentionCount; i++) extra += inlineToken("M", i);
  return content + extra;
}

export function insertInlineToken(
  content: string,
  caret: number,
  kind: "A" | "M",
  index: number,
): { value: string; caret: number } {
  const safe = Math.max(0, Math.min(caret, content.length));
  const tok = inlineToken(kind, index);
  return {
    value: content.slice(0, safe) + tok + content.slice(safe),
    caret: safe + tok.length,
  };
}

export function stripRange(
  content: string,
  start: number,
  end: number,
): { value: string; caret: number } {
  const a = Math.max(0, Math.min(start, end, content.length));
  const b = Math.max(0, Math.min(Math.max(start, end), content.length));
  return { value: content.slice(0, a) + content.slice(b), caret: a };
}

export function dropInlineIndex(
  content: string,
  kind: "attachment" | "mention",
  index: number,
): string {
  return serializeInlineBody(
    parseInlineBody(content).flatMap((s) => {
      if (s.kind !== kind) return [s];
      if (s.index === index) return [];
      if (s.index > index) return [{ ...s, index: s.index - 1 }];
      return [s];
    }),
  );
}

export function reconcileInlineBody<
  A extends { id: string },
  M extends { id: string },
>(
  content: string,
  attachments: readonly A[],
  mentions: readonly M[],
): { value: string; attachments: A[]; mentions: M[] } {
  const nextAtts: A[] = [];
  const nextMents: M[] = [];
  const spans: InlineSpan[] = [];
  for (const span of parseInlineBody(content)) {
    if (span.kind === "text") {
      spans.push(span);
      continue;
    }
    if (span.kind === "attachment") {
      const item = attachments[span.index];
      if (!item) continue;
      spans.push({ kind: "attachment", index: nextAtts.length });
      nextAtts.push(item);
      continue;
    }
    const item = mentions[span.index];
    if (!item) continue;
    spans.push({ kind: "mention", index: nextMents.length });
    nextMents.push(item);
  }
  return {
    value: serializeInlineBody(spans),
    attachments: nextAtts,
    mentions: nextMents,
  };
}

const KIND_LABEL: Record<string, string> = {
  file: "文件",
  dir: "文件夹",
  conversation: "对话",
};

function nameOf(
  item: { name?: string; path?: string } | undefined,
  fallback: string,
): string {
  const raw = item?.name || item?.path || fallback;
  return raw.trim() || fallback;
}

function roleOf(
  item: { role?: string; agentId?: string; agent_id?: string } | undefined,
  fallback: string,
): string {
  const raw = item?.role || item?.agentId || item?.agent_id || fallback;
  return raw.trim() || fallback;
}

/** History / copy / title preview: keep order, never re-inject file bodies. */
export function renderInlineLabels(
  content: string,
  attachments: ReadonlyArray<{
    name?: string;
    path?: string;
    kind?: string;
  }> | null,
  mentions: ReadonlyArray<{
    role?: string;
    agentId?: string;
    agent_id?: string;
  }> | null,
): string {
  const atts = attachments ?? [];
  const ments = mentions ?? [];
  let out = "";
  for (const span of parseInlineBody(content)) {
    if (span.kind === "text") {
      out += span.text;
      continue;
    }
    if (span.kind === "attachment") {
      const att = atts[span.index];
      if (!att) continue;
      const label = KIND_LABEL[att.kind ?? "file"] ?? "文件";
      out += `[${label} ${nameOf(att, String(span.index))}]`;
      continue;
    }
    const mention = ments[span.index];
    if (!mention) continue;
    out += `[点名 ${roleOf(mention, String(span.index))}]`;
  }
  return out;
}
