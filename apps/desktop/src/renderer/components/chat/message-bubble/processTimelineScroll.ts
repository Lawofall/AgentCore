import { createContext } from "react";

/**
 * Scroll parent for process-row windowing. Only the run-detail inspector
 * provides this — the CEO bubble stays fully mapped (short captain lane).
 */
export const ProcessTimelineScrollContext = createContext<HTMLElement | null>(
  null,
);
