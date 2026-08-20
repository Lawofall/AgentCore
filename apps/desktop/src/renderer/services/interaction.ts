import { api } from "@/services/api";
import { getActiveSidecarTarget } from "@/services/sidecarRouting";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

/**
 * Cloud settle POST wall clock. Must stay well under the server Local channel
 * op budget (~59s) so transient NetworkError can still be retried in-process
 * before the awaiter times out (dogfood: settle NetworkError → sticky dead).
 */
export const INTERACTION_RESOLVE_TIMEOUT_MS = 15_000;

/**
 * Where the paused interaction awaits settle.
 *
 * Explicit — never inferred from `activeSidecarTurns`. A conversation can have a
 * live sidecar turn **and** cloud-bridged CLIENT_TOOL ops (device fulfill stream)
 * at once; guessing by conversationId mis-routes cloud settles into the sidecar
 * registry (`{resolved:false}` → server waits → false "channel dead").
 */
export type InteractionSettleOrigin = "cloud" | "sidecar";

/**
 * 收口回执。`already_processed` = 这张卡在服务端**已经结了**——多端同权下先到先得，后到的
 * 那端不能当作自己结的（会替用户认领一个他没做过的动作），也不能静静地什么都不发生。
 *
 * 回执只说「结了」，不带 `status` / `arbitrated_by`：升级卡撞上主管仲裁或超时兜底时同样返回
 * 它，所以**谁结的**只能等带线材字段的 `*_resolved` 帧去证（B2 · 验收 5）。
 */
export type ResolveInteractionOutcome = "settled" | "already_processed";

/**
 * Unified suspend-resume bridge (§18.2): a single endpoint settles any client-resolvable
 * paused interaction — a tool approval, a local-workspace op, a worker's blocking
 * escalation, or an interactive debate round. The body is discriminated on `kind`, so
 * callers build their kind-specific shape.
 *
 * 挂起即收口 (②, Phase 3): `ask_user` / `plan_review` are no longer settled here — a CEO
 * checkpoint finalizes the turn and is continued via the cold `POST .../resume` path
 * (services/turns.ts), so their resolve schemas are gone from the backend union.
 */
export type ResolveInteractionBody =
  | Schemas["ResolveApprovalInteraction"]
  | Schemas["ResolveClientToolInteraction"]
  | Schemas["ResolveEscalationInteraction"];

/**
 * Settle a paused hot-path interaction over the transport that owns the awaiter.
 *
 * The single choke point for live resolve kinds (approval / client_tool / escalation) —
 * NOT ask_user / plan_review / team_preview (cold resume).
 *
 * - **`origin: "sidecar"`** → `window.sidecarApi.respond` (in-process sidecar registry).
 * - **`origin: "cloud"`** → POST the unified resolve endpoint (cloud InteractionRegistry,
 *   including CLIENT_TOOL ops delivered on the device fulfill stream).
 */
export async function resolveInteraction(
  conversationId: string,
  interactionId: string,
  body: ResolveInteractionBody,
  origin: InteractionSettleOrigin,
): Promise<ResolveInteractionOutcome> {
  if (origin === "sidecar") {
    const sidecarTarget = getActiveSidecarTarget(conversationId);
    if (!sidecarTarget) {
      throw new Error("本地回合未激活，无法结算交互");
    }
    const reply = await window.sidecarApi.respond({
      rootId: sidecarTarget.rootId,
      subpath: sidecarTarget.subpath,
      requestId: interactionId,
      conversationId,
      result: body,
    });
    // Sidecar mirrors cloud 404 as `{resolved:false}`. Do NOT map this to ApiError(404):
    // decideEscalation swallows 404 (race with a real timeout), which would leave the card
    // spinning while the worker is still waiting — the exact「点了继续、后端没收到」failure.
    // Surface a real error so the card re-enables and the user can retry.
    if (reply && reply.resolved === false) {
      throw new Error("交互请求不存在或已处理，请重试");
    }
    return "settled";
  }
  const receipt = await api.post<{ status?: string } | null>(
    `/v1/conversations/${conversationId}/interactions/${interactionId}`,
    body,
    INTERACTION_RESOLVE_TIMEOUT_MS,
  );
  return receipt?.status === "already_processed"
    ? "already_processed"
    : "settled";
}
