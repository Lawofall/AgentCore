import { apiFetch } from "@/api/client";
// Settle a paused interaction over the LIVE SSE stream (交互式暂停放行).
//
// POST to the unified resolve endpoint wakes the awaiter on the open stream.
// REST body types track OpenAPI (cloud-only on mobile — no sidecar branch).
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

/** Settle a paused interaction — discriminated on `kind` (OpenAPI union).
 *
 * 挂起即收口 (②): `ask_user` / `plan_review` / `team_preview` settle via cold resume.
 * Hot path: approval / escalation / debate_round.
 */
export type ResolveInteractionBody =
  | Schemas["ResolveApprovalInteraction"]
  | Schemas["ResolveEscalationInteraction"];

/**
 * 收口结果。`already_processed` = 这张卡在服务端已经结掉了——多端同权下先到先得，
 * 后到的那端得如实说「已由另一端处理」，而不是静静地什么都不发生（B2 · 验收 5）。
 */
export type ResolveOutcome = "settled" | "already_processed";

/**
 * POST a paused interaction's answer; the live SSE stream resumes.
 * 200 `{status:"already_processed"}`（后端 registry 已 settle）与 404（挂起项已不在）
 * 都归 `already_processed`：卡是陈旧的，调用方据此收口文案。
 */
export async function resolveInteraction(
  conversationId: string,
  interactionId: string,
  body: ResolveInteractionBody,
): Promise<ResolveOutcome> {
  const res = await apiFetch(
    `/v1/conversations/${conversationId}/interactions/${interactionId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (res.status === 404) return "already_processed";
  if (!res.ok) {
    throw new Error(`放行失败 (${res.status})`);
  }
  const payload = (await res.json().catch(() => null)) as {
    status?: string;
  } | null;
  return payload?.status === "already_processed"
    ? "already_processed"
    : "settled";
}

/**
 * 阻塞式求决策 (§4.5): the two calls a user can make on a worker's blocking escalation —
 * answer it, or 按假设继续 (degrade to the worker's stated assumption, == a timeout). UNLIKE
 * plan_review there is no 停止: ending the whole turn is the CEO `ask_user` job, not one
 * worker's escalation. Mirrors the desktop `decideEscalation` (services/escalation.ts).
 */
export type EscalationUserDecision =
  | { kind: "answer"; answer: string }
  | { kind: "use_assumption" };

/**
 * POST the user's call on a worker's blocking escalate to the SAME unified resolve endpoint
 * (keyed by `escalation_id`). The suspending tool's awaiter — never this route — emits
 * `escalation_resolved` on the live stream, which folds the run's pending escalation to
 * resolved/timeout and unmounts the card. 已被另一端结掉时返回 `already_processed`
 * （见 {@link resolveInteraction}）；其它失败照抛，卡自己恢复可点。
 */
export function decideEscalation(
  conversationId: string,
  escalationId: string,
  decision: EscalationUserDecision,
): Promise<ResolveOutcome> {
  return resolveInteraction(conversationId, escalationId, {
    kind: "escalation",
    answer: decision.kind === "answer" ? decision.answer : "",
    use_assumption: decision.kind === "use_assumption",
    // Mobile 尚未接写权移交 UI；契约字段必填，恒 false。
    transfer_ownership: false,
  });
}
