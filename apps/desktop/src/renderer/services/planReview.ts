/** Decisions a user can actively make on a plan_review card.
 * plan_review: `continue` / `adjust` / `stop`.
 * `research_first` is stage_card (先调研)；cold resume does not send kickoff veto fields.
 * `timeout` is engine-only and never sent by the client.
 *
 * Settlement is cold `POST .../resume` (services/turns.ts `runResume`). */
export type PlanReviewUserDecision =
  | "continue"
  | "adjust"
  | "stop"
  | "research_first";
