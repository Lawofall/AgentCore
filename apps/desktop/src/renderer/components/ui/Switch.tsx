interface SwitchProps {
  /** Controlled on/off state. */
  checked: boolean;
  /** Fired on toggle (click / keyboard), with the next value. */
  onCheckedChange: (checked: boolean) => void;
  /** Accessible name — used as `aria-label` when no visible <label> wraps it. */
  label?: string;
  disabled?: boolean;
}

/**
 * Minimal controlled pill switch — the project's reusable on/off primitive (no
 * shadcn/Radix dep). Tokenised track + knob with symmetric travel via inner
 * padding; exposes `role="switch"` + `aria-checked` for a11y. Sized for caption
 * rows (`h-5 w-9`), consistent with `desktop-layout.mdc` / `color-tokens.mdc`.
 */
export function Switch({
  checked,
  onCheckedChange,
  label,
  disabled,
}: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={`inline-flex h-5 w-9 shrink-0 items-center rounded-full p-0.5 transition-colors disabled:opacity-40 ${
        checked ? "bg-primary" : "bg-muted"
      }`}
    >
      <span
        className={`size-4 rounded-full bg-background shadow-raised transition-transform ${
          checked ? "translate-x-4" : "translate-x-0"
        }`}
      />
    </button>
  );
}
