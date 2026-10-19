import { ZOOM_DEFAULT, applyFontSize, applyZoom } from "@/lib/appearance";
import { useUIStore } from "@/stores/ui";
import { useEffect } from "react";

/**
 * Keep font-scale and zoom in sync with the UI store. Mounted next to
 * `useApplyTheme`. Narrow / Capacitor keep zoom at 100% (layout would
 * collapse); the stored value is left alone for the next wide session.
 */
export function useApplyAppearance(forceDefaultZoom = false): void {
  const fontSize = useUIStore((s) => s.fontSize);
  const zoom = useUIStore((s) => s.zoom);
  useEffect(() => {
    applyFontSize(fontSize);
    applyZoom(forceDefaultZoom ? ZOOM_DEFAULT : zoom);
  }, [fontSize, zoom, forceDefaultZoom]);
}
