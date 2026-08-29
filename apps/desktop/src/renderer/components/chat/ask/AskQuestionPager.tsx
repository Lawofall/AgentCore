/**
 * 多题澄清卡头右侧编号跳转。各题可点切换（没写补充也能切）。
 * 不是问卷 Wizard：无进度条、不禁止回看、无第二套题目 accordion。
 * 非末题主 CTA 文案是「下一题」不是「下一步」。仅 `questions.length ≥ 2` 时挂上。
 * 提交仍须每题有勾选或人话——编号切题 ≠ 交卡。
 */
import { ASK_ROW_TONE } from "./AskOptionRow";

/** First question index the user has not entered; `null` when every index is visited. */
export function firstUnvisitedAskIndex(
  total: number,
  visited: ReadonlySet<number>,
): number | null {
  for (let i = 0; i < total; i++) {
    if (!visited.has(i)) return i;
  }
  return null;
}

/** Primary footer action for a paged generic clarify card. */
export function resolveAskPrimaryAction(
  questionCount: number,
  step: number,
  visited: ReadonlySet<number>,
): { type: "advance" | "jump"; index: number } | { type: "submit" } {
  if (questionCount < 2) return { type: "submit" };
  const last = questionCount - 1;
  if (step < last) return { type: "advance", index: step + 1 };
  const jump = firstUnvisitedAskIndex(questionCount, visited);
  if (jump !== null) return { type: "jump", index: jump };
  return { type: "submit" };
}

export function AskQuestionPager({
  total,
  index,
  onChange,
  disabled = false,
  visited,
}: {
  total: number;
  index: number;
  onChange: (index: number) => void;
  disabled?: boolean;
  /** Indices the user has already entered. Unvisited stay clickable (browse ≠ submit). */
  visited: ReadonlySet<number>;
}) {
  if (total < 2) return null;

  const numbers = Array.from({ length: total }, (_, i) => i + 1);

  return (
    <fieldset
      className="m-0 flex items-center gap-0.5 border-0 p-0"
      disabled={disabled}
    >
      <legend className="sr-only">切换问题</legend>
      {numbers.map((n) => {
        const current = n - 1 === index;
        const seen = visited.has(n - 1);
        return (
          <button
            key={n}
            type="button"
            aria-current={current ? "true" : undefined}
            aria-label={`第 ${n} 题，共 ${total} 题`}
            disabled={disabled}
            onClick={() => onChange(n - 1)}
            className={`flex size-6 shrink-0 items-center justify-center rounded-lg text-xs font-medium tabular-nums focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-40 ${
              current
                ? ASK_ROW_TONE.markActive
                : seen
                  ? "bg-muted text-muted-foreground hover:bg-accent hover:text-foreground"
                  : "bg-muted/70 text-muted-foreground/80 hover:bg-accent hover:text-foreground"
            }`}
          >
            {n}
          </button>
        );
      })}
    </fieldset>
  );
}
