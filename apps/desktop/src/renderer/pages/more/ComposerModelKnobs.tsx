import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";
import { type ReactNode, useState } from "react";

export type ComposerChoice = {
  id: string;
  label: string;
  value: string;
  items: readonly { value: string; label: string }[];
  onChange: (token: string) => void;
};

export type ComposerKnobsProps = {
  effort?: ComposerChoice | null;
  budget?: ComposerChoice | null;
  busy?: boolean;
  slots?: ReactNode;
};

/** 浮卡旋钮行：字段名不截，右侧值占剩余宽度、不够才截。 */
export function TuneRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="shrink-0 text-sm text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

export const TUNE_TRIGGER_CLASS =
  "inline-flex min-w-0 flex-1 items-center justify-end gap-1 rounded-lg px-2 py-1 text-sm text-foreground hover:bg-accent disabled:opacity-60";

function DropdownTune({
  label,
  value,
  items,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  items: readonly { value: string; label: string }[];
  disabled?: boolean;
  onChange: (token: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const valueLabel = items.find((item) => item.value === value)?.label ?? value;
  return (
    <div>
      <TuneRow label={label}>
        <button
          type="button"
          aria-label={label}
          aria-expanded={open}
          disabled={disabled}
          onClick={() => setOpen((next) => !next)}
          className={cn(TUNE_TRIGGER_CLASS, open && "bg-accent")}
        >
          <span className="min-w-0 truncate" title={valueLabel}>
            {valueLabel}
          </span>
          <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
        </button>
      </TuneRow>
      {open ? (
        <div className="mt-2 overflow-hidden rounded-lg border border-border">
          {items.map((item) => (
            <button
              key={item.value}
              type="button"
              className={cn(
                "flex w-full items-center px-3 py-1.5 text-left text-sm",
                item.value === value
                  ? "bg-accent text-foreground"
                  : "text-foreground hover:bg-accent/50",
              )}
              onClick={() => {
                onChange(item.value);
                setOpen(false);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** 输入框浮卡旋钮：思考强度与上下文长度一律下拉。组队队员同一套行。 */
export function ComposerKnobs({
  effort,
  budget,
  busy,
  slots,
}: ComposerKnobsProps) {
  if (!effort && !budget && !slots) return null;
  return (
    <div className="mt-3 space-y-2">
      {effort ? (
        <DropdownTune
          label={effort.label}
          value={effort.value}
          items={effort.items}
          disabled={busy}
          onChange={effort.onChange}
        />
      ) : null}
      {budget ? (
        <DropdownTune
          label={budget.label}
          value={budget.value}
          items={budget.items}
          disabled={busy}
          onChange={budget.onChange}
        />
      ) : null}
      {slots}
    </div>
  );
}
