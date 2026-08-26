import { cn } from "@/lib/utils";
import {
  type InputHTMLAttributes,
  type TextareaHTMLAttributes,
  forwardRef,
} from "react";

/** Keyboard ring + mouse border — L2 层叠与焦点. */
export const fieldFocusClass =
  "focus:border-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** Shared form-control surface (border + radius + focus ring) — also the base
 *  for `Select`, so every text-ish field reads identically. */
export const fieldSurfaceClass = `rounded-lg border border-border bg-background text-sm text-foreground placeholder:text-muted-foreground ${fieldFocusClass} disabled:opacity-40`;

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(function Input({ className, ...props }, ref) {
  return (
    <input
      ref={ref}
      className={cn(fieldSurfaceClass, "h-8 px-2.5", className)}
      {...props}
    />
  );
});

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      className={cn(
        fieldSurfaceClass,
        "resize-none px-2.5 py-1.5 text-xs max-md:text-sm",
        className,
      )}
      {...props}
    />
  );
});
