/**
 * Cold-path resume shell — thin re-export so callers keep
 * `@/components/chat/ResumePrompt` / `./ResumePrompt`.
 *
 * Implementation lives under `./resume/` aligned with hot cards
 * (`CheckpointCard` + `ask/`；plan_review 只在拍板卡).
 */
export { ResumePrompt } from "./resume/ResumePrompt";
