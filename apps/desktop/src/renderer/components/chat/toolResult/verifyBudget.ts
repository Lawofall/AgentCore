/**
 * Incomplete (idle hang / disaster wall / unknown timeout):
 * ``display.budget_exceeded === true``.
 * Warning chrome — not the same red fault path as a real verify fail.
 * Face copy branches on ``timeout_kind``; this flag is only the incomplete ≠ fault switch.
 */
export function isVerifyBudgetExceeded(display: unknown): boolean {
  if (!display || typeof display !== "object") return false;
  return (display as { budget_exceeded?: unknown }).budget_exceeded === true;
}

const IDLE_FACE = "执行无响应（无输出已中止）";
const DISASTER_FACE = "执行已强制中止";
const INCOMPLETE_FACE = "验证未完成";

/** Collapsed peek / expand banner when incomplete. Idle and disaster have their own copy;
 *  ``budget_exceeded`` without ``timeout_kind`` stays generic 验证未完成. */
export function verifyIncompleteFace(display: unknown): string {
  if (!isVerifyBudgetExceeded(display)) return "";
  const kind = (display as { timeout_kind?: unknown }).timeout_kind;
  if (kind === "idle") return IDLE_FACE;
  if (kind === "disaster") return DISASTER_FACE;
  return INCOMPLETE_FACE;
}
