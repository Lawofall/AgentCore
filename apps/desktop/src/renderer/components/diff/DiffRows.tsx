import {
  type DiffChromeKind,
  diffRowClass,
  diffSign,
  diffSignClass,
} from "@/lib/diffChrome";

export type DiffRow = { type: DiffChromeKind; text: string };

export type DiffRowsProps = { lines: readonly DiffRow[] };

/** Row list only — card chrome stays at the call site. */
export function DiffRows({ lines }: DiffRowsProps) {
  return (
    <>
      {lines.map((l, i) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: a diff render is a stable, positional list — the index is the natural key and rows never reorder within a render.
          key={i}
          className={`flex ${diffRowClass(l.type)}`}
        >
          <span
            className={`w-5 shrink-0 self-stretch select-none text-center ${diffSignClass(l.type)}`}
          >
            {diffSign(l.type)}
          </span>
          <span className="whitespace-pre-wrap break-words pr-2">
            {l.text || " "}
          </span>
        </div>
      ))}
    </>
  );
}
