import { useUIStore } from "@/stores/ui";
import type { CSSProperties } from "react";
import { Toaster as SonnerToaster } from "sonner";

// Remap sonner's internal color variables onto our design tokens so toasts adopt
// the app surface (and flip with `.dark`) instead of sonner's built-in palette.
// Inline so it wins over sonner's theme-scoped defaults; tokens are OKLCH, used
// raw per color-tokens (no hsl() wrapper). `--border-radius` lifts sonner's 8px
// default to our `rounded-xl` (12px) for cards/panels/popovers.
const tokenVars = {
  "--normal-bg": "var(--popover)",
  "--normal-text": "var(--popover-foreground)",
  "--normal-border": "var(--border)",
  "--border-radius": "0.75rem",
} as CSSProperties;

/**
 * App-wide toast host. Mounted once at the root so any module (services, stores,
 * components) can fire a toast via lib/toast without prop-drilling. Errors and
 * successes share the neutral popover surface; the status color rides on the
 * leading icon supplied by lib/toast.
 */
export function Toaster() {
  const theme = useUIStore((s) => s.theme);
  return (
    <SonnerToaster
      position="bottom-right"
      theme={theme}
      style={tokenVars}
      toastOptions={{
        classNames: {
          title: "line-clamp-2 break-words",
          description: "line-clamp-2 break-words text-muted-foreground!",
          actionButton:
            "bg-primary! text-primary-foreground! rounded-lg! font-medium!",
        },
      }}
    />
  );
}
