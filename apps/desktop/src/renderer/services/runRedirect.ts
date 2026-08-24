import { api } from "@/services/api";
import { resolveSidecarControlTargetForEngine } from "@/services/sidecarRouting";
import { useConversationStore } from "@/stores/conversation";
import type { InterveneAck } from "@agentcore/protocol-fold-kit";

export interface SubmitRunRedirectParams {
  executionId: string;
  runId: string;
  feedback: string;
}

/** 服务端回执：受理与否 + 一句话原因（`queued` 只是排队计数，不代表这次被受理）。 */
export type RunRedirectAck = InterveneAck & { queued: number };

/**
 * Redirect one in-flight worker (中间可见性 Phase 2a).
 *
 * Local turns route to the sidecar process (the cloud HTTP POST cannot reach the
 * in-process queue). The drive loop drains this queue on its next cancel poll:
 * the worker is cancelled outright, then re-run with the feedback — hot
 * `continue_run` from its salvaged transcript when continuable, else a
 * same-role `_redir` handoff from scratch (`runtime/delegate/drive_redirect.py`).
 * Nothing about it is「排队等下一步」——say so in any UI confirmation.
 *
 * 回执 `accepted` 说的是引擎有没有真收下：够不着这个 run 时它是 false，此时**什么都
 * 没发生**，界面不许说「已改方向」。
 */
export async function submitRunRedirect(
  conversationId: string,
  params: SubmitRunRedirectParams,
): Promise<RunRedirectAck> {
  const sidecarTarget = await resolveSidecarControlTargetForEngine(
    conversationId,
    useConversationStore.getState().byId[conversationId]?.executionVia,
  );
  if (sidecarTarget) {
    return window.sidecarApi.runRedirect({
      rootId: sidecarTarget.rootId,
      subpath: sidecarTarget.subpath,
      conversationId,
      executionId: params.executionId,
      runId: params.runId,
      feedback: params.feedback,
    });
  }
  return api.post<RunRedirectAck>(
    `/v1/conversations/${conversationId}/run-redirect`,
    {
      execution_id: params.executionId,
      run_id: params.runId,
      feedback: params.feedback,
    },
  );
}

/** Why a run's terminal outcome was recorded as accepted (跑一半改方向 Step 4).
 *  User-facing offer is only ``redirect_ignored``; the other two stay audit-only. */
export type RunOutcomeReason =
  | "deterministic_failure"
  | "redirect_ignored"
  | "recovery_ignored";

export interface AcceptRunOutcomeParams {
  /** The assistant message (turn) the run belongs to — scopes the audit trail. */
  messageId: string;
  runId: string;
  reason: RunOutcomeReason;
  executionId?: string;
  note?: string;
}

/**
 * Record the user's explicit accept of a run's terminal outcome (跑一半改方向 Step 4 · 忽略路径收口).
 *
 * Replaces the old frontend-only「忽略」(clearExecution) with a durable「用户主动接受此结果」row on
 * the SAME owner-scoped audit trail the run detail reads — so the acceptance survives reload and is
 * auditable. Cloud-only (like the audit read): the record lives with the turn's other audit rows.
 * Idempotent server-side; returns `recorded=false` if this run's outcome was already accepted.
 */
export async function acceptRunOutcome(
  conversationId: string,
  params: AcceptRunOutcomeParams,
): Promise<{ recorded: boolean }> {
  return api.post<{ ok: boolean; recorded: boolean; action: string }>(
    `/v1/conversations/${conversationId}/messages/${params.messageId}/accept-outcome`,
    {
      run_id: params.runId,
      reason: params.reason,
      execution_id: params.executionId,
      note: params.note,
    },
  );
}
