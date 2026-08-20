/**
 * 行式选项 —— 统一 ask 卡的核心视觉形。
 *
 * 与旧 {@link OptionButton} 的差别是「去盒子化」：行本身无边框，靠发丝分隔线成组；
 * hover / 键盘焦点才浮出整行圆角灰底 + 右侧 →；选中态 = 整行灰底 + 左侧序号方块反白。
 * 分隔线随「活动行」隐藏其上下两条，灰底才不会被线切开（这条细节决定了整组的观感）。
 *
 * 「推荐 / 默认」不再做彩色徽章：`default` 项已由 {@link useAskAnswer} 预选，**选中态即其
 * 表达**，再挂一枚「默认」chip 是纯冗余。只有 `recommended` 与 `default` 不是同一项时，才在
 * 行右补一个灰色小字（{@link AskRow.hint}）—— 由调用方判断后传入，本组件不认识这两个字段。
 *
 * 多选组不另设右侧勾选框：左侧方块在选中时把序号换成 ✓，一行只保留一个状态锚点。
 */
import { interactiveCheckpointTone } from "@/components/ui/tone-presets";
import { ArrowRight, Check } from "lucide-react";
import { type ReactNode, useRef, useState } from "react";
import type { AskTone } from "./AskUserFields";

/** 灰阶为主 —— 卡内不出现品牌色，强调只靠选中态。 */
export const ASK_ROW_TONE = interactiveCheckpointTone.neutral;

export type AskRow = {
  /** React key + 焦点身份；通常用 option.label。 */
  key: string;
  label: string;
  /** 第二行补充说明。 */
  detail?: string;
  /** 行右侧灰色小字（如「推荐」）。彩色徽章已废弃，只走这里。 */
  hint?: string;
  /** 取代左侧序号（绑定文件夹的文件夹图标）。 */
  icon?: ReactNode;
  /** 标签走占位色。 */
  muted?: boolean;
  selected: boolean;
  disabled?: boolean;
  onSelect: () => void;
};

export function AskRowGroup({
  rows,
  multiple = false,
  tone = ASK_ROW_TONE,
  className = "",
}: {
  rows: AskRow[];
  /** 多选组：选中的行左侧方块显示 ✓ 而非序号。 */
  multiple?: boolean;
  tone?: AskTone;
  className?: string;
}) {
  // hover 与键盘焦点共用一个「活动行」，分隔线的显隐规则两者一致。
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const moveFocus = (delta: number, from: number) => {
    const next = (from + delta + rows.length) % rows.length;
    rowRefs.current[next]?.focus();
  };

  return (
    <div className={className}>
      {rows.map((row, i) => (
        <div key={row.key}>
          <RowDivider
            hidden={i === 0 || activeIdx === i || activeIdx === i - 1}
          />
          <OptionRow
            ref={(el) => {
              rowRefs.current[i] = el;
            }}
            row={row}
            index={i}
            multiple={multiple}
            tone={tone}
            onActive={(on) => setActiveIdx(on ? i : null)}
            onMove={(d) => moveFocus(d, i)}
          />
        </div>
      ))}
    </div>
  );
}

function RowDivider({ hidden }: { hidden: boolean }) {
  return (
    <div
      aria-hidden
      className={`mx-2 h-px ${hidden ? "bg-transparent" : "bg-border/60"}`}
    />
  );
}

function OptionRow({
  ref,
  row,
  index,
  multiple,
  tone,
  onActive,
  onMove,
}: {
  ref: (el: HTMLButtonElement | null) => void;
  row: AskRow;
  index: number;
  multiple: boolean;
  tone: AskTone;
  onActive: (on: boolean) => void;
  onMove: (delta: number) => void;
}) {
  const { label, detail, hint, icon, muted, selected, disabled, onSelect } =
    row;
  return (
    <button
      ref={ref}
      type="button"
      disabled={disabled}
      aria-pressed={selected}
      onClick={onSelect}
      onMouseEnter={() => onActive(true)}
      onMouseLeave={() => onActive(false)}
      onFocus={() => onActive(true)}
      onBlur={() => onActive(false)}
      onKeyDown={(e) => {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          onMove(1);
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          onMove(-1);
        }
      }}
      className={`group flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left focus:outline-none disabled:opacity-40 ${
        selected ? "bg-muted" : "hover:bg-accent focus-visible:bg-accent"
      }`}
    >
      <span
        className={`flex size-6 shrink-0 items-center justify-center rounded-lg text-xs font-medium ${
          selected ? tone.markActive : "bg-muted text-muted-foreground"
        }`}
        aria-hidden
      >
        {icon ??
          (selected && multiple ? (
            <Check size={12} strokeWidth={3} />
          ) : (
            index + 1
          ))}
      </span>
      <span className="min-w-0 flex-1">
        <span
          className={`block text-xs ${muted ? "text-muted-foreground" : "text-foreground"}`}
        >
          {label}
        </span>
        {detail && (
          <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">
            {detail}
          </span>
        )}
      </span>
      {hint && (
        <span className="shrink-0 text-xs text-muted-foreground">{hint}</span>
      )}
      {!multiple && (
        <ArrowRight
          size={14}
          aria-hidden
          className="shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100"
        />
      )}
    </button>
  );
}
