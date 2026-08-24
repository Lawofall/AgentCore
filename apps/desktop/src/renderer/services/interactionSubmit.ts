import { notifyError, notifyInfo } from "@/lib/toast";
import { ApiError } from "@/services/api";
import { resolveInteraction } from "@/services/interaction";
import type { ResolveInteractionBody } from "@/services/interaction";
import type { PlanReviewUserDecision } from "@/services/planReview";
import { isPausedFrameGone, runResume } from "@/services/turns";
import {
  INTERACTION_SUBMIT_PATH,
  useInteractionStore,
} from "@/stores/interactions";
import type { InteractionKind } from "@/types/interactionExt";

/** True when the API says this interaction is no longer answerable. */
export function isInteractionOrphanedError(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  if (err.status !== 410) return false;
  if (err.code === "interaction_orphaned") return true;
  // FastAPI detail={code:...} body shape
  try {
    const parsed = JSON.parse(err.body) as {
      detail?: { code?: string } | string;
    };
    if (
      typeof parsed.detail === "object" &&
      parsed.detail?.code === "interaction_orphaned"
    ) {
      return true;
    }
  } catch {
    /* ignore */
  }
  return false;
}

export function isPendingInteractionsAwaitingError(err: unknown): boolean {
  if (!(err instanceof ApiError) && !(err && typeof err === "object"))
    return false;
  const status =
    err instanceof ApiError
      ? err.status
      : "status" in err
        ? Number((err as { status?: number }).status)
        : undefined;
  const code =
    err instanceof ApiError
      ? err.code
      : "code" in err
        ? String((err as { code?: string }).code ?? "")
        : undefined;
  if (status === 409 && code === "pending_interactions_awaiting") return true;
  if (err instanceof ApiError) {
    try {
      const parsed = JSON.parse(err.body) as {
        detail?: { code?: string };
        error?: { code?: string };
      };
      const detailCode =
        typeof parsed.detail === "object" ? parsed.detail?.code : undefined;
      return (
        err.status === 409 &&
        (detailCode === "pending_interactions_awaiting" ||
          parsed.error?.code === "pending_interactions_awaiting")
      );
    } catch {
      return false;
    }
  }
  return false;
}

export const PENDING_INTERACTIONS_HINT =
  "有待拍板的确认卡，先处理或停止当前任务";

export type SubmitInteractionResult =
  | "ok"
  | "orphaned"
  | "busy"
  | "already_settled";

/** User-visible copy when submitInteraction returns a non-ok status. */
export function submitInteractionFeedback(
  result: Exclude<SubmitInteractionResult, "ok">,
): string {
  if (result === "orphaned") return "确认已失效";
  // 谁处理的说不了（回执不带处理方），只如实说清「这张卡结了、AI 在往下走」。
  if (result === "already_settled") return "这张卡已经处理过了，AI 已继续";
  return "请稍候再试";
}

/**
 * 提交没走成时的提示。`already_settled` 不是错——多端同权下卡被先到的那端结掉是常态，
 * 报成红色失败会让用户以为出了故障。
 */
export function notifySubmitInteractionResult(
  result: Exclude<SubmitInteractionResult, "ok">,
): void {
  const copy = submitInteractionFeedback(result);
  if (result === "already_settled") {
    notifyInfo(copy);
    return;
  }
  notifyError(copy);
}

export type HotSubmitBody = ResolveInteractionBody;

export interface ColdSubmitArgs {
  messageId: string;
  decision: PlanReviewUserDecision;
  note: string;
  selected?: string[];
}

/**
 * Unified submit path (方案 §3.2): kind → cold | hot | stage.
 *
 * Hot: interactions.beginSubmit gates double-submit.
 * Cold: authority is InteractionStore cold pending (ResumePrompt paint + submit).
 * pausedTurns remains recovery/`setForConversation` routing shell + origin
 * fallback — do not require a pausedTurns frame to submit when IX has the entry.
 * Dedup = caller local submitting + Interaction beginSubmit when tracked.
 *
 * 热路「已经结了」的回执（`already_processed` / 404）→ `already_settled`：卡收起来不再可点
 * （多端同权下另一端可能早就点掉了，放回可点只会一点再点、次次 404），但**不认领**结果与
 * 处理方——回执证不了人（升级卡的 404 也可能是主管接管仲裁），归属只认线材帧。
 *
 * 冷路没有这一档：云端幂等成功现在回 200 + EPHEMERAL `resume_settled`（SSE 侧把卡收成
 * 结果态），所以走到这里的 404 是诚实失效，按作废处理。
 */
export async function submitInteraction(args: {
  id: string;
  kind: InteractionKind;
  conversationId: string;
  hotBody?: HotSubmitBody;
  cold?: ColdSubmitArgs;
}): Promise<SubmitInteractionResult> {
  const path = INTERACTION_SUBMIT_PATH[args.kind];
  const store = useInteractionStore.getState();

  if (path === "hot") {
    if (!store.beginSubmit(args.id)) return "busy";
    try {
      if (!args.hotBody) {
        store.reopen(args.id);
        throw new Error("缺少热路提交体");
      }
      const receipt = await resolveInteraction(
        args.conversationId,
        args.id,
        args.hotBody,
        store.get(args.id)?.origin === "sidecar" ? "sidecar" : "cloud",
      );
      if (receipt === "already_processed") {
        store.markSettledByReceipt({
          kind: args.kind,
          id: args.id,
          conversationId: args.conversationId,
        });
        return "already_settled";
      }
      // Optimistic resolved; matching *_resolved SSE is idempotent.
      // Keep hotBody as resolution so grant_delegation / decision UI can read it
      // before the resolved SSE arrives.
      store.markResolved({
        kind: args.kind,
        id: args.id,
        resolution: args.hotBody as unknown as Record<string, unknown>,
      });
      return "ok";
    } catch (err) {
      if (isInteractionOrphanedError(err)) {
        store.markOrphaned(args.id);
        return "orphaned";
      }
      // 404 = 服务端已经没有这张待办了。它多半是被先到的那端结掉的（多端同权），说成
      // 「卡失效了」既不准也误导；收起来、如实说结了，谁结的等线材帧。
      if (err instanceof ApiError && err.status === 404) {
        store.markSettledByReceipt({
          kind: args.kind,
          id: args.id,
          conversationId: args.conversationId,
        });
        return "already_settled";
      }
      store.reopen(args.id);
      throw err;
    }
  }

  // cold — never gate on interactions presence
  if (!args.cold) {
    throw new Error("缺少冷路提交参数");
  }
  const tracked = store.get(args.id)?.status === "pending";
  if (tracked) store.beginSubmit(args.id);

  try {
    await runResume(
      args.cold.messageId,
      args.cold.decision,
      args.cold.note,
      args.cold.selected,
      {
        conversationId: args.conversationId,
      },
    );
    // 帧早被上一次续跑吃掉时（`resume_settled`）这次点击并不是那条 settlement——
    // SSE 侧已经按 journal 的事实收好卡了，别再用本端刚点的决策盖回去冒充结果。
    if (!useInteractionStore.getState().get(args.id)?.resumeSettled) {
      store.markResolved({
        kind: args.kind,
        id: args.id,
        resolution: {
          decision: args.cold.decision,
          note: args.cold.note,
          selected: args.cold.selected ?? [],
        },
      });
    }
    return "ok";
  } catch (err) {
    if (isInteractionOrphanedError(err)) {
      store.markOrphaned(args.id);
      return "orphaned";
    }
    // 挂起帧真的没了（超保留期被清理 / 回合已重新生成或删除；sidecar 的
    // PAUSED_TURN_NOT_FOUND 同）。「已被别人处理」不再走这里——那条现在是 200 +
    // `resume_settled`，由 SSE 侧收成结果态。这里剩下的是诚实失效：卡作废（不是「被答了」，
    // 更不是「这次没发出去」），放回可点只会请用户一点再点、次次 404。runResume 挂的横幅
    // 已经说清了到底是哪种失效，保留它。
    if (isPausedFrameGone(err)) {
      store.markOrphaned(args.id, {
        kind: args.kind,
        conversationId: args.conversationId,
        messageId: args.cold.messageId,
      });
      return "orphaned";
    }
    if (tracked) store.reopen(args.id);
    throw err;
  }
}
