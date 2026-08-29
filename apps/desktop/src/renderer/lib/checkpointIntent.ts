/** Unrecognized checkpoint intent (including retired ask shells) → decision. */
export type AskUiIntent = "decision" | "organize_plan" | "daily_review";

/** Normalize wire/recovery `intent` — only the three chrome intents; all else → decision. */
export function parseCheckpointIntent(raw: unknown): AskUiIntent {
  if (raw === "organize_plan" || raw === "daily_review") {
    return raw;
  }
  return "decision";
}
