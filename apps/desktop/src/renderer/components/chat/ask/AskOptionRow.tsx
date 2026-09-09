/**
 * 行式选项 —— 统一 ask 卡的核心视觉形。
 *
 * 与旧 {@link OptionButton} 的差别是「去盒子化」：行本身无边框，靠发丝分隔线成组；
 * hover / 键盘焦点才浮出整行圆角灰底 + 右侧 →；选中态 = 整行灰底 + 左侧序号方块反白。
 * 分隔线随「活动行」隐藏其上下两条，灰底才不会被线切开（这条细节决定了整组的观感）。
 *
 * 打开不预选 `default`，认同须再点一下。行右灰色小字（{@link AskRow.hint}）留给通用提示
 * （例如「默认」）—— 由调用方传入，本组件不认识 default 字段。
 *
 * 多选组不另设右侧勾选框：左侧方块在选中时把序号换成 ✓，一行只保留一个状态锚点。
 *
 * 本题人话（{@link AskRowGroup} `note`）接在选项组末行：铅笔、不编号、无 →。
 * 它不是第 N+1 个选项——写人不取消已选，点选项不清空已写。
 */
import { interactiveCheckpointTone } from "@/components/ui/tone-presets";
import { ArrowRight, Check, Pencil } from "lucide-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type { AskTone } from "./AskUserFields";

/** 灰阶为主 —— 卡内不出现品牌色，强调只靠选中态。 */
export const ASK_ROW_TONE = interactiveCheckpointTone.neutral;

export type AskRow = {
  /** React key + 焦点身份；通常用 option.label。 */
  key: string;
  label: string;
  /** 第二行补充说明。 */
  detail?: string;
  /** 行右侧灰色小字（如「默认」）。彩色徽章已废弃，只走这里。 */
  hint?: string;
  /** 取代左侧序号（绑定文件夹的文件夹图标）。 */
  icon?: ReactNode;
  /** 标签走占位色。 */
  muted?: boolean;
  selected: boolean;
  disabled?: boolean;
  onSelect: () => void;
};

/** 选项组末行人话。不是可点选的第 N+1 项。 */
export type AskNoteSlot = {
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
  placeholder: string;
};

export function AskRowGroup({
  rows,
  multiple = false,
  tone = ASK_ROW_TONE,
  className = "",
  note,
}: {
  rows: AskRow[];
  /** 多选组：选中的行左侧方块显示 ✓ 而非序号。 */
  multiple?: boolean;
  tone?: AskTone;
  className?: string;
  note?: AskNoteSlot;
}) {
  // hover 与键盘焦点共用一个「活动行」，分隔线的显隐规则两者一致。
  // 人话行的活动下标是 rows.length，用来藏它和末选项之间的发丝。
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const noteRef = useRef<HTMLTextAreaElement | null>(null);
  const noteIndex = rows.length;

  const moveFocus = (delta: number, from: number) => {
    if (note && delta === 1 && from === rows.length - 1) {
      noteRef.current?.focus();
      return;
    }
    const next = (from + delta + rows.length) % rows.length;
    rowRefs.current[next]?.focus();
  };

  useEffect(() => {
    const first = rowRefs.current.find((el) => el && !el.disabled);
    first?.focus();
  }, []);

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
      {note && (
        <div>
          <RowDivider
            hidden={
              rows.length === 0 ||
              activeIdx === noteIndex ||
              activeIdx === noteIndex - 1
            }
          />
          <NoteRow
            ref={(el) => {
              noteRef.current = el;
            }}
            value={note.value}
            onChange={note.onChange}
            disabled={note.disabled}
            placeholder={note.placeholder}
            onActive={(on) => setActiveIdx(on ? noteIndex : null)}
            onMoveUp={() => {
              if (rows.length === 0) return;
              rowRefs.current[rows.length - 1]?.focus();
            }}
          />
        </div>
      )}
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

/** 选项组末行人话：与选项同行高 / 发丝，铅笔占序号位，无 →。 */
function NoteRow({
  ref,
  value,
  onChange,
  disabled,
  placeholder,
  onActive,
  onMoveUp,
}: {
  ref: (el: HTMLTextAreaElement | null) => void;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
  placeholder: string;
  onActive: (on: boolean) => void;
  onMoveUp: () => void;
}) {
  const innerRef = useRef<HTMLTextAreaElement | null>(null);
  const setRef = (el: HTMLTextAreaElement | null) => {
    innerRef.current = el;
    ref(el);
  };

  const resize = useCallback(() => {
    const el = innerRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: value is an intentional re-run key
  useEffect(() => {
    resize();
  }, [value, resize]);

  return (
    <label
      data-ask-note-row=""
      className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left ${
        disabled ? "opacity-40" : "hover:bg-accent focus-within:bg-accent"
      }`}
      onMouseEnter={() => onActive(true)}
      onMouseLeave={() => {
        if (innerRef.current !== document.activeElement) onActive(false);
      }}
    >
      <span
        className="flex size-6 shrink-0 items-center justify-center text-muted-foreground"
        aria-hidden
      >
        <Pencil size={14} />
      </span>
      <textarea
        ref={setRef}
        rows={1}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => onActive(true)}
        onBlur={() => onActive(false)}
        onKeyDown={(e) => {
          if (e.key !== "ArrowUp" || e.shiftKey) return;
          const el = e.currentTarget;
          if (el.selectionStart !== 0 || el.selectionEnd !== 0) return;
          e.preventDefault();
          onMoveUp();
        }}
        className="min-w-0 flex-1 resize-none border-0 bg-transparent p-0 text-xs leading-5 text-foreground placeholder:text-muted-foreground/70 focus:outline-none disabled:cursor-not-allowed"
      />
    </label>
  );
}
