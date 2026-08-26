/**
 * 多题澄清卡头右侧编号跳转。随机访问，不是 Wizard：无「下一步」、不拦未访题。
 * 仅 `questions.length ≥ 2` 时由 {@link AskDecisionBody} 挂上。
 */
import { ASK_ROW_TONE } from "./AskOptionRow";

export function AskQuestionPager({
  total,
  index,
  onChange,
  disabled = false,
}: {
  total: number;
  index: number;
  onChange: (index: number) => void;
  disabled?: boolean;
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
        return (
          <button
            key={n}
            type="button"
            aria-current={current ? "true" : undefined}
            aria-label={`第 ${n} 题，共 ${total} 题`}
            onClick={() => onChange(n - 1)}
            className={`flex size-6 shrink-0 items-center justify-center rounded-lg text-xs font-medium tabular-nums focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-40 ${
              current
                ? ASK_ROW_TONE.markActive
                : "bg-muted text-muted-foreground hover:bg-accent hover:text-foreground"
            }`}
          >
            {n}
          </button>
        );
      })}
    </fieldset>
  );
}
