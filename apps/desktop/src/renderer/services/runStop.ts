import { api } from "@/services/api";
import { resolveSidecarControlTargetForEngine } from "@/services/sidecarRouting";
import { useConversationStore } from "@/stores/conversation";
import type { InterveneAck } from "@agentcore/protocol-fold-kit";

export interface SubmitRunStopParams {
  executionId: string;
  /** Omit / null = stop every in-flight & queued worker under this execution. */
  runId?: string | null;
}

/** 服务端回执：受理与否 + 一句话原因（`queued` 只是排队计数，不代表这次被受理）。 */
export type RunStopAck = InterveneAck & { queued: number };

/**
 * Ask the engine to stop a mid-flight worker (does **not** cancel the turn or CEO).
 *
 * Routing mirrors ``submitRunRedirect``:
 * - **Local (sidecar) turn**（含活 map 已空、引擎仍在跑）→ ``sidecarApi.runStop``
 * - **Cloud turn** → ``POST …/run-stop``
 *
 * Not fire-and-forget: the response says whether a live drive loop actually took
 * it. 够不着的 run（驱动已退出 / 不在当前计划里）不入队，回执里明说——别再拿整条
 * 执行的排队计数当「引擎将停下这位队员」。
 */
export async function submitRunStop(
  conversationId: string,
  params: SubmitRunStopParams,
): Promise<RunStopAck> {
  const sidecarTarget = await resolveSidecarControlTargetForEngine(
    conversationId,
    useConversationStore.getState().byId[conversationId]?.executionVia,
  );
  if (sidecarTarget) {
    return window.sidecarApi.runStop({
      rootId: sidecarTarget.rootId,
      subpath: sidecarTarget.subpath,
      conversationId,
      executionId: params.executionId,
      runId: params.runId ?? null,
    });
  }
  return api.post<RunStopAck>(`/v1/conversations/${conversationId}/run-stop`, {
    execution_id: params.executionId,
    run_id: params.runId ?? null,
  });
}
