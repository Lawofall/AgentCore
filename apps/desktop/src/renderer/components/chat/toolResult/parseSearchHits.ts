/**
 * Parse grep / code_search tool output into plain lines vs clickable hit rows.
 * Formats (backend text contract, no structured metadata this batch):
 * - grep: `path:line: text`
 * - code_search: `path:start-end …` (header line of each chunk)
 */

export type SearchHitKind = "grep" | "code_search";

export type SearchHitSegment =
  | { type: "plain"; text: string }
  | {
      type: "hit";
      path: string;
      /** First line of the hit (grep line_no / code_search start). */
      line: number;
      /** code_search end line; absent for grep. */
      endLine?: number;
      /** Remainder after the path:line prefix (match text / symbol + lang). */
      rest: string;
    };

/** Captures `: text` after line so the colon is preserved when re-rendering. */
const GREP_HIT = /^(.+?):(\d+)(: ?.*)$/;
const CODE_SEARCH_HIT = /^(.+?):(\d+)-(\d+)(.*)$/;

export function isSearchHitTool(toolName: string): toolName is SearchHitKind {
  return toolName === "grep" || toolName === "code_search";
}

/** Basename for side-panel `showFile(path, name)`. */
export function searchHitFileName(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || path;
}

/**
 * Line-oriented parse. Non-hit lines (summary, empty-result tips, snippet
 * indents, truncation footers) stay `plain`.
 */
export function parseSearchHits(
  text: string,
  kind: SearchHitKind,
): SearchHitSegment[] {
  if (!text) return [];
  const lines = text.split("\n");
  return lines.map((line) => parseOneLine(line, kind));
}

function parseOneLine(line: string, kind: SearchHitKind): SearchHitSegment {
  if (kind === "grep") {
    const m = GREP_HIT.exec(line);
    if (m) {
      return {
        type: "hit",
        path: m[1],
        line: Number(m[2]),
        rest: m[3] ?? "",
      };
    }
  } else {
    const m = CODE_SEARCH_HIT.exec(line);
    if (m) {
      return {
        type: "hit",
        path: m[1],
        line: Number(m[2]),
        endLine: Number(m[3]),
        rest: m[4] ?? "",
      };
    }
  }
  return { type: "plain", text: line };
}

export function hasSearchHits(segments: SearchHitSegment[]): boolean {
  return segments.some((s) => s.type === "hit");
}

/** Path label shown on the link: `path:line` or `path:start-end`. */
export function searchHitPathLabel(
  hit: Extract<SearchHitSegment, { type: "hit" }>,
): string {
  if (hit.endLine != null) return `${hit.path}:${hit.line}-${hit.endLine}`;
  return `${hit.path}:${hit.line}`;
}
