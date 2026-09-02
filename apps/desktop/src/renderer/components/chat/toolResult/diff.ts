/** A line's role in a two-sided diff: unchanged `context`, removed (`del`,
 * old-only), or inserted (`add`, new-only). */
export type DiffLineType = "context" | "add" | "del";

export interface DiffLine {
  type: DiffLineType;
  text: string;
}

/** Above this old×new line-product an exact LCS diff isn't worth its O(n·m) cost
 * (and a giant str_replace is rare) — fall back to a whole-block replace so the
 * UI never janks. */
const LCS_CELL_BUDGET = 250_000;

/**
 * Minimal line-level diff (`old_string` → `new_string`) for the str_replace edit
 * card (工具结果富渲染). Pure + dependency-free so it unit-tests in isolation and
 * runs in the render path without pulling a diff library.
 *
 * Uses a classic LCS so unchanged lines stay as shared `context` and only the
 * real edits show as `del` / `add` (red / green). For an outsized edit it skips
 * the LCS and emits every old line as `del` then every new line as `add` — still
 * correct, just coarser.
 */
export function lineDiff(oldText: string, newText: string): DiffLine[] {
  const a = oldText.split("\n");
  const b = newText.split("\n");
  const n = a.length;
  const m = b.length;

  if (n * m > LCS_CELL_BUDGET) {
    return [
      ...a.map((text): DiffLine => ({ type: "del", text })),
      ...b.map((text): DiffLine => ({ type: "add", text })),
    ];
  }

  // dp[i][j] = LCS length of a[i:] and b[j:]; walked forwards to recover the path.
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] =
        a[i] === b[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ type: "context", text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ type: "del", text: a[i] });
      i++;
    } else {
      out.push({ type: "add", text: b[j] });
      j++;
    }
  }
  while (i < n) {
    out.push({ type: "del", text: a[i] });
    i++;
  }
  while (j < m) {
    out.push({ type: "add", text: b[j] });
    j++;
  }
  return out;
}

/** Add/delete line counts from {@link lineDiff} — ToolLine title stat, omit-zero at render. */
export function lineDiffCounts(
  oldText: string,
  newText: string,
): { adds: number; dels: number } {
  let adds = 0;
  let dels = 0;
  for (const line of lineDiff(oldText, newText)) {
    if (line.type === "add") adds++;
    else if (line.type === "del") dels++;
  }
  return { adds, dels };
}
