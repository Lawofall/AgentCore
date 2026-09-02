/**
 * file_read result footers from the server (`_format_line_window`):
 * `（全文 N 行）` or `（第 a–b 行，共 N 行[；已达行顶|字符顶|未达安全顶，省略 limit 可整读]）`.
 * Title chrome only wants the window range (`42–53 行`), not the file total;
 * the expanded body strips the footer so it isn't a second scroll-to-end label.
 */

export type FileReadLineWindow = {
  start: number;
  end: number;
  total: number;
};

const FULL = /^（全文 (\d+) 行）$/;
const WINDOW =
  /^（第 (\d+)[–-](\d+) 行，共 (\d+) 行(?:；(?:已达(?:行顶|字符顶)|未达安全顶，省略 limit 可整读))?）$/;

function matchFooter(line: string): FileReadLineWindow | null {
  const full = line.match(FULL);
  if (full) {
    const total = Number(full[1]);
    return { start: 1, end: total, total };
  }
  const win = line.match(WINDOW);
  if (!win) return null;
  return {
    start: Number(win[1]),
    end: Number(win[2]),
    total: Number(win[3]),
  };
}

function footerLineIndex(lines: string[]): number {
  let seen = 0;
  for (let i = lines.length - 1; i >= 0; i--) {
    const t = lines[i].trim();
    if (!t) continue;
    if (matchFooter(t)) return i;
    if (++seen >= 4) break;
  }
  return -1;
}

export function parseFileReadWindow(
  result: string | null,
): FileReadLineWindow | null {
  if (!result) return null;
  const lines = result.split("\n");
  const idx = footerLineIndex(lines);
  if (idx < 0) return null;
  return matchFooter(lines[idx]?.trim() ?? "");
}

/** True when the window is not the complete file — the only case the title shows. */
export function isPartialFileReadWindow(w: FileReadLineWindow): boolean {
  return !(w.start === 1 && w.end === w.total);
}

export function stripFileReadFooter(result: string): string {
  const lines = result.split("\n");
  const idx = footerLineIndex(lines);
  if (idx < 0) return result;
  lines.splice(idx, 1);
  return lines
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trimEnd();
}
