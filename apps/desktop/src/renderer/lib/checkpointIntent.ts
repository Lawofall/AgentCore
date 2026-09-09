/** Unrecognized checkpoint intent (including retired ask shells) → decision. */
export type AskUiIntent = "decision" | "organize_plan";

/** Normalize wire/recovery `intent` — only organize_plan keeps chrome; all else → decision. */
export function parseCheckpointIntent(raw: unknown): AskUiIntent {
  if (raw === "organize_plan") {
    return raw;
  }
  return "decision";
}
