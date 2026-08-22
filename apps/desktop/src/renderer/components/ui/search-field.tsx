import { IconButton } from "@/components/ui/icon-button";
import { fieldFocusClass } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Search, X } from "lucide-react";
import {
  type InputHTMLAttributes,
  type KeyboardEvent,
  forwardRef,
  useCallback,
} from "react";

export type SearchFieldSize = "sm" | "md";
export type SearchFieldVariant = "field" | "plain";

const SIZE_CLASS: Record<SearchFieldSize, string> = {
  sm: "h-8 text-sm",
  md: "h-9 text-sm",
};

const ICON_OFFSET: Record<SearchFieldSize, string> = {
  sm: "pl-7",
  md: "pl-9",
};

const CLEAR_OFFSET: Record<SearchFieldSize, string> = {
  sm: "pr-7",
  md: "pr-9",
};

const END_PADDING: Record<SearchFieldSize, string> = {
  sm: "pr-2.5",
  md: "pr-3",
};

const ICON_POS: Record<SearchFieldSize, string> = {
  sm: "left-2",
  md: "left-3",
};

const ICON_SIZE: Record<SearchFieldSize, number> = {
  sm: 14,
  md: 16,
};

export interface SearchFieldProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "size" | "onChange"> {
  variant?: SearchFieldVariant;
  size?: SearchFieldSize;
  value: string;
  onValueChange: (value: string) => void;
  /** Show × when non-empty. Defaults to true for `field`, false for `plain`. */
  clearable?: boolean;
  /** Escape clears when non-empty (does not stop propagation). Default true. */
  escapeClears?: boolean;
  className?: string;
  inputClassName?: string;
}

/**
 * Scoped list/tree filter or popover option filter — not global search (→ SearchTrigger + Cmd+K).
 * `field` = bordered input with icon; `plain` = borderless row inside an existing popover shell.
 */
export const SearchField = forwardRef<HTMLInputElement, SearchFieldProps>(
  function SearchField(
    {
      variant = "field",
      size = "sm",
      value,
      onValueChange,
      clearable,
      escapeClears = true,
      className,
      inputClassName,
      onKeyDown,
      ...props
    },
    ref,
  ) {
    const showClear = clearable ?? variant === "field";
    const iconSize = ICON_SIZE[size];

    const handleClear = useCallback(() => {
      onValueChange("");
    }, [onValueChange]);

    const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
      if (escapeClears && e.key === "Escape" && value) {
        e.preventDefault();
        handleClear();
      }
      onKeyDown?.(e);
    };

    if (variant === "plain") {
      return (
        <div
          className={cn("flex min-w-0 flex-1 items-center gap-2", className)}
        >
          <Search
            size={iconSize}
            className="shrink-0 text-muted-foreground"
            aria-hidden
          />
          <input
            ref={ref}
            value={value}
            onChange={(e) => onValueChange(e.target.value)}
            onKeyDown={handleKeyDown}
            className={cn(
              "min-w-0 flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none",
              inputClassName,
            )}
            {...props}
          />
          {showClear && value && (
            <IconButton
              size="sm"
              onClick={handleClear}
              aria-label="清除筛选"
              className="shrink-0 text-muted-foreground hover:text-foreground"
            >
              <X size={13} />
            </IconButton>
          )}
        </div>
      );
    }

    return (
      <div className={cn("relative min-w-0", className)}>
        <Search
          size={iconSize}
          className={cn(
            "pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted-foreground",
            ICON_POS[size],
          )}
          aria-hidden
        />
        <input
          ref={ref}
          value={value}
          onChange={(e) => onValueChange(e.target.value)}
          onKeyDown={handleKeyDown}
          className={cn(
            "w-full rounded-lg border border-border bg-background text-foreground placeholder:text-muted-foreground",
            fieldFocusClass,
            SIZE_CLASS[size],
            ICON_OFFSET[size],
            showClear && value ? CLEAR_OFFSET[size] : END_PADDING[size],
            inputClassName,
          )}
          {...props}
        />
        {showClear && value && (
          <IconButton
            size="sm"
            onClick={handleClear}
            aria-label="清除筛选"
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <X size={13} />
          </IconButton>
        )}
      </div>
    );
  },
);
