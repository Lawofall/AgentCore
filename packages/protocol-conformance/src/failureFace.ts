// Empty-failure face — protocol-level "must the user see a face?" predicate.
// Render clients own product copy; this package only judges presence from the
// folded ProjectedTurn (翻转默认：结构化错误 / 失败收尾 → 有脸；短豁免表除外).

import type { ProjectedTurn } from "./projectedTurn";

/** Interaction kinds that already own a dedicated pause / ask UI surface. */
const DEDICATED_PAUSE_ASK_KINDS = new Set([
  "checkpoint",
  "plan_review",
  "approval",
  "stage_card",
]);

/**
 * True when a folded turn must present a non-empty failure face.
 *
 * Default ON when content is empty and there is structured ``error`` or a
 * failure-ish ``finishReason``. Short exemption list:
 * - user-initiated stop (``cancelled``)
 * - ``paused`` when a dedicated pause/ask interaction card already owns the UI
 */
export function hasProjectedFailureFace(p: ProjectedTurn): boolean {
  const empty = !(p.content ?? "").trim();
  const structured =
    !!(p.error?.message?.trim() || p.error?.code?.trim()) &&
    p.error?.code !== "TURN_CANCELLED";

  if (structured) {
    // User-stop code alone is silent; any other structured error → face.
    return true;
  }

  if (!empty) {
    // Body present: only hard ``error`` finish (without payload) still needs a face.
    return p.finishReason === "error";
  }

  const fr = p.finishReason;
  if (fr === "cancelled") return false;
  if (fr === "paused") {
    const hasCard = p.interactions.some(
      (i) =>
        (i.status === "pending" || i.status === "resolved") &&
        DEDICATED_PAUSE_ASK_KINDS.has(i.kind),
    );
    return !hasCard;
  }
  return (
    fr === "error" ||
    fr === "degraded" ||
    fr === "unproductive" ||
    fr === "interrupted"
  );
}
