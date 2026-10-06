/**
 * LLM math delimiters → remark-math dollars.
 *
 * remark-math only sees `$` / `$$`. CommonMark treats `\(` as an escape, so
 * the usual chat pipeline (LobeChat / LibreChat / assistant-ui) rewrites
 * `\(...\)` / `\[...\]` on the raw string before parse, and skips fenced and
 * inline code. This scanner is that rewrite: left to right, an outer `\[`
 * owns the span, an unclosed opener stays prose.
 *
 * Those four sequences are delimiters, not math commands. Models often wrap a
 * symbol again inside an open span (`\[ ... \(t\) ... \]`). KaTeX then throws
 * `Can't use function '\(' in math mode`, and rehype-katex paints the whole
 * formula as red source. Unwrap the inner pair and keep the tex. Do not
 * rewrite it to `$t$`: `$` is illegal in math mode as well.
 */

export type TexMathSpan = {
  from: number;
  to: number;
  tex: string;
  display: boolean;
};

function isEscaped(source: string, i: number): boolean {
  let n = 0;
  for (let k = i - 1; k >= 0 && source[k] === "\\"; k--) n++;
  return n % 2 === 1;
}

function atLineStart(source: string, i: number): boolean {
  return i === 0 || source[i - 1] === "\n";
}

function fenceMarkerAt(
  source: string,
  i: number,
): { char: "`" | "~"; len: number } | null {
  let j = i;
  let spaces = 0;
  while (spaces < 4 && source[j] === " ") {
    j++;
    spaces++;
  }
  const ch = source[j];
  if (ch !== "`" && ch !== "~") return null;
  let len = 0;
  while (source[j + len] === ch) len++;
  if (len < 3) return null;
  return { char: ch, len };
}

/** End index after a matching inline-code span; -1 if this backtick is not a fence. */
function skipInlineCode(source: string, i: number): number {
  const n = source.length;
  let len = 0;
  while (i + len < n && source[i + len] === "`") len++;
  if (len === 0) return -1;
  let j = i + len;
  while (j < n) {
    if (source[j] === "`") {
      let k = 0;
      while (j + k < n && source[j + k] === "`") k++;
      if (k === len) return j + k;
      j += k;
      continue;
    }
    if (source[j] === "\n") return -1;
    j++;
  }
  return -1;
}

function findClose(source: string, from: number, close: "\\)" | "\\]"): number {
  const n = source.length;
  let j = from;
  while (j < n) {
    if (atLineStart(source, j) && fenceMarkerAt(source, j)) return -1;
    if (source.startsWith(close, j) && !isEscaped(source, j)) return j;
    j++;
  }
  return -1;
}

function texDelimiterAt(
  source: string,
  i: number,
): { close: "\\)" | "\\]" } | null {
  if (source[i] !== "\\" || isEscaped(source, i)) return null;
  const next = source[i + 1];
  if (next === "(") return { close: "\\)" };
  if (next === "[") return { close: "\\]" };
  return null;
}

/** Matching closer for one already-open delimiter, counting same-type nesting. */
function findMatchingClose(
  source: string,
  from: number,
  close: "\\)" | "\\]",
): number {
  const open = close === "\\)" ? "\\(" : "\\[";
  let depth = 1;
  let j = from;
  while (j < source.length) {
    if (source[j] === "\\" && !isEscaped(source, j)) {
      if (source.startsWith(open, j)) {
        depth++;
        j += 2;
        continue;
      }
      if (source.startsWith(close, j)) {
        depth--;
        if (depth === 0) return j;
        j += 2;
        continue;
      }
    }
    j++;
  }
  return -1;
}

function unwrapNestedTexDelimiters(tex: string): string {
  let out = "";
  let i = 0;
  while (i < tex.length) {
    const open = texDelimiterAt(tex, i);
    if (open) {
      const closeAt = findMatchingClose(tex, i + 2, open.close);
      if (closeAt !== -1) {
        out += unwrapNestedTexDelimiters(tex.slice(i + 2, closeAt));
        i = closeAt + 2;
        continue;
      }
    }
    out += tex[i];
    i++;
  }
  return out;
}

export function findTexMathSpans(source: string): TexMathSpan[] {
  const spans: TexMathSpan[] = [];
  const n = source.length;
  let i = 0;
  let fence: { char: "`" | "~"; len: number } | null = null;

  while (i < n) {
    if (atLineStart(source, i)) {
      const marker = fenceMarkerAt(source, i);
      if (marker) {
        if (!fence) {
          fence = marker;
        } else if (marker.char === fence.char && marker.len >= fence.len) {
          fence = null;
        }
        const nl = source.indexOf("\n", i);
        i = nl === -1 ? n : nl + 1;
        continue;
      }
    }
    if (fence) {
      i++;
      continue;
    }

    if (source[i] === "`") {
      const end = skipInlineCode(source, i);
      if (end !== -1) {
        i = end;
        continue;
      }
    }

    if (source[i] === "\\" && !isEscaped(source, i)) {
      const next = source[i + 1];
      if (next === "(" || next === "[") {
        const display = next === "[";
        const close = display ? "\\]" : "\\)";
        const closeAt = findClose(source, i + 2, close);
        if (closeAt !== -1) {
          const tex = unwrapNestedTexDelimiters(source.slice(i + 2, closeAt));
          if (tex.trim()) {
            spans.push({
              from: i,
              to: closeAt + 2,
              tex,
              display: display || tex.includes("\n"),
            });
            i = closeAt + 2;
            continue;
          }
        }
      }
    }

    i++;
  }
  return spans;
}

/** Rewrite `\(`/`\[` into `$` / `$$` for remark-math. Idempotent on dollar math. */
export function texDelimitersToDollars(source: string): string {
  const spans = findTexMathSpans(source);
  if (spans.length === 0) return source;
  let out = "";
  let i = 0;
  for (const span of spans) {
    out += source.slice(i, span.from);
    if (span.display) {
      out += `$$${span.tex}$$`;
    } else {
      out += `$${span.tex.trim()}$`;
    }
    i = span.to;
  }
  out += source.slice(i);
  return out;
}
