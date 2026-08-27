import { bumpConversationCache } from "@/hooks/useConversations";
import {
  StreamError,
  describeStreamError,
  streamErrorAction,
} from "@/lib/errors";
import type { PlanReviewUserDecision } from "@/services/planReview";
import {
  conversationHasColdPending,
  isClientOnlyResumeKey,
  resolveResumeMessageId,
  resolveResumeOrigin,
} from "@/services/resume";
import { clearSidecarHealth, probeSidecar } from "@/services/sidecarHealth";
import {
  type SidecarTarget,
  getActiveSidecarTarget,
  resolveConversationLocalTarget,
} from "@/services/sidecarRouting";
import {
  type OutgoingAgentMention,
  type OutgoingAttachment,
  regenerateConversation,
  resumeConversation,
} from "@/services/streamConversation";
import { resumeConversationViaSidecar } from "@/services/streamConversationViaSidecar";
import {
  type AgentMentionMeta,
  type MessageAttachmentMeta,
  getRuntime,
  useConversationStore,
} from "@/stores/conversation";
import { beginTurnPreflight } from "@/stores/conversation/turnPhaseActions";
import { clearInteractionPrompts } from "@/stores/interactionPrompts";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import {
  finalizeGeneratingIfNeeded,
  finalizeHonestStopAbort,
  isAbort,
  isTransportDrop,
} from "./helpers";
import { cancelRejoinLiveTurn, rejoinLiveTurn } from "./recovery";

/** Durable resume routes to the local sidecar engine when the frame lives there. */
function shouldResumeViaSidecar(origin: "sidecar" | "server"): boolean {
  return origin === "sidecar";
}

/**
 * 续跑本机帧的寻址：跟本地事实，**忽略**显式强制关（`sidecarPreference==="off"`）。
 * 优先活回合登记，否则会话本地绑定（勿用 `resolveSidecarRoot`——强制关早退会挡续跑）。
 */
async function resolveResumeSidecarTarget(
  conversationId: string,
): Promise<SidecarTarget | null> {
  const active = getActiveSidecarTarget(conversationId);
  if (active) {
    return { rootId: active.rootId, subpath: active.subpath };
  }
  return resolveConversationLocalTarget(conversationId);
}

/**
 * Resume request was refused before any SSE opened (404/409/410/5xx, or sidecar
 * never started). Transient refusals may restore the optimistic-removed resume
 * card; frame-gone refusals must not (协议：帧不存在应丢卡).
 * Mid-stream drops / user abort are NOT refusals — the turn may already be running.
 */
function isResumeRequestRefused(err: unknown): boolean {
  if (!(err instanceof StreamError)) return false;
  if (err.kind === "http" && err.status != null) {
    const s = err.status;
    return s === 404 || s === 409 || s === 410 || s >= 500;
  }
  if (err.kind !== "sidecar") return false;
  // 启动期可降级拒：恢复冷卡。
  if (err.recoverable === true) return true;
  // 忙槽互斥（非引擎故障、不降级）：仍恢复冷卡，让用户稍候再点 / 等 deferred。
  return err.code === "sidecar_turn_busy";
}

/**
 * 挂起帧真的不在了——保持卡已丢的状态，只出一条横幅（不给一键重试：重试必然再失败）。
 *
 * 「已被别人处理」**不再**走这条路：云端幂等成功现在回 200 + EPHEMERAL `resume_settled`，
 * 由 SSE 侧把卡收成结果态。留在这里的是诚实失效——云端 404/410（超保留期被清理 / 回合已
 * 重新生成或删除）与 sidecar 的 `PAUSED_TURN_NOT_FOUND`（本地引擎尚未跟进幂等契约）。
 * 唯一用处是决定「这次拒绝要不要把乐观丢掉的壳放回去」：帧不存在就不该放回。
 */
export function isPausedFrameGone(err: unknown): boolean {
  if (!(err instanceof StreamError)) return false;
  if (err.kind === "http" && (err.status === 404 || err.status === 410)) {
    return true;
  }
  // sidecar：JSON-RPC 的 code 过不了 IPC（只剩 message），所以本地引擎那条
  // `PAUSED_TURN_NOT_FOUND(-32003)` 只能靠它自己的文案认。云端已不再发这句
  // （幂等成功走 `resume_settled`，真失效的 404 另有两句更准的话）。
  const msg = `${err.serverMessage ?? ""} ${err.message ?? ""}`;
  if (msg.includes("挂起的回合不存在或已处理")) return true;
  if (msg.includes("PAUSED_TURN_NOT_FOUND") || /-32003\b/.test(msg)) {
    return true;
  }
  const code = (err.code ?? "").toLowerCase();
  return code === "not_found" || code === "paused_turn_not_found";
}

function toOutgoingAttachments(
  atts: readonly MessageAttachmentMeta[],
): OutgoingAttachment[] {
  return atts.map((a) => ({
    name: a.name,
    path: a.path,
    text: "",
    truncated: a.truncated,
    kind: a.kind ?? "file",
    conversation_id: a.conversationId,
    workspace_path: a.workspacePath,
  }));
}

function toOutgoingMentions(
  ments: readonly AgentMentionMeta[],
): OutgoingAgentMention[] {
  return ments.map((m) => ({ agent_id: m.agentId, role: m.role }));
}

export type RegenerateMaterials = {
  attachments: readonly MessageAttachmentMeta[];
  agentMentions: readonly AgentMentionMeta[];
};

/**
 * Re-run a turn from an existing (persisted) user message.
 *
 * Backs the message-level regenerate / edit-and-resend actions. Drops everything
 * after the user message, opens a fresh assistant bubble, then streams the new
 * reply; the backend truncates the same range so persisted history stays
 * consistent. On a transport failure it raises an error banner (no one-click
 * regenerate from the banner — bubble regenerate remains).
 */
export async function runRegenerate(
  userMessageId: string,
  content?: string,
  materials?: RegenerateMaterials,
): Promise<void> {
  const store = useConversationStore.getState();
  const conversationId = store.currentConversationId;
  if (!conversationId || getRuntime(conversationId).isGenerating) return;

  cancelRejoinLiveTurn(conversationId);
  // Route every turn write to this conversation's slice by id, not the active
  // key — the user may switch away mid-stream and the turn keeps running in the
  // background (switchConversation no longer aborts it).
  store.clearError(conversationId);
  bumpConversationCache(conversationId);
  store.truncateAfter(userMessageId, conversationId);
  store.createAssistantMessage(conversationId);

  const ac = new AbortController();
  store.setAbort(ac, conversationId);
  beginTurnPreflight(conversationId);
  try {
    await regenerateConversation({
      conversationId,
      messageId: userMessageId,
      content,
      attachments: materials
        ? toOutgoingAttachments(materials.attachments)
        : undefined,
      agentMentions: materials
        ? toOutgoingMentions(materials.agentMentions)
        : undefined,
      replaceMaterials: materials !== undefined,
      signal: ac.signal,
    });
  } catch (err) {
    if (isAbort(err)) {
      finalizeHonestStopAbort(conversationId);
      return;
    }
    // A mid-stream drop no longer means the turn died (1a: it runs detached) —
    // rejoin it live (1b) rather than regenerating, which would double-run it.
    if (isTransportDrop(err) && (await rejoinLiveTurn(conversationId))) return;
    finalizeGeneratingIfNeeded(conversationId);
    // A failed turn never delivers `approval_resolved`; drop this conversation's
    // paused prompt (other conversations keep theirs).
    clearInteractionPrompts(conversationId);
    const msg = describeStreamError(err);
    if (msg) {
      useConversationStore
        .getState()
        .setError(msg, null, conversationId, streamErrorAction(err));
    }
  } finally {
    useConversationStore.getState().setAbort(null, conversationId);
  }
}

export interface RunResumeOptions {
  /**
   * 这张卡所属的会话。缺省 = 当前打开的那个。
   *
   * 调用方**知道**卡属于哪个会话时必须显式传：卡不在当前会话里（画布 / 浮窗，或用户已经
   * 切走）时，本函数挂的错误横幅与调用方清的错误横幅会落在两条不同的会话上，红条从此
   * 清不掉。
   */
  conversationId?: string;
}

/**
 * Continue a durably-paused turn (结构化挂起 2b resume) and stream the continuation.
 *
 * The turn paused at a plan_review / ask_user checkpoint and was persisted, then
 * lost its live stream (disconnect / restart). The user's decision (continue /
 * adjust / stop) — plus any ask_user option `selected` — is POSTed to the resume
 * endpoint, which claims the frame and drives the rest of the turn on a fresh SSE.
 * No new user message — resume reuses the paused assistant bubble (same turn id /
 * projection key).
 *
 * Card lifecycle: remove the resume card as soon as the request is about to fire;
 * restore it only on transient refusals before any stream opens (409/5xx / sidecar
 * never started). Frame-gone refusals (404/410 / PAUSED_TURN_NOT_FOUND) keep the
 * card dropped. Mid-stream interrupt and user abort leave the card gone (the turn
 * is already running — rejoin / banner paths handle that).
 *
 * Sidecar frames never degrade to cloud resume (双模式工作区 §10.4 — cloud has no
 * local frame → guaranteed 404). Missing sidecar target or failed probe keeps the
 * card and raises a plain banner (no one-click resume from the banner).
 */
export async function runResume(
  messageId: string,
  decision: PlanReviewUserDecision,
  note: string,
  selected: string[] = [],
  opts: RunResumeOptions = {},
): Promise<void> {
  const store = useConversationStore.getState();
  const conversationId = opts.conversationId ?? store.currentConversationId;
  if (!conversationId) {
    throw new Error("resume blocked: no active conversation");
  }
  // D9：冷卡与 live 可合法共存。冷卡在位时不抹 generating，直接发 resume
  // （忙槽由服务端收下决策并推 EPHEMERAL `resume_deferred`）。无冷卡的中途流式仍拦截。
  const liveGeneratingAtStart = getRuntime(conversationId).isGenerating;
  if (liveGeneratingAtStart && !conversationHasColdPending(conversationId)) {
    store.setError(
      "当前回合仍在生成中，请稍后再点继续",
      null,
      conversationId,
      null,
    );
    throw new Error("resume blocked: turn is still generating");
  }

  cancelRejoinLiveTurn(conversationId);
  store.clearError(conversationId);
  bumpConversationCache(conversationId);

  // Card may still key a client bubble id while the bubble already has a stamp —
  // rekey to the server id before pending lookup / POST (only truly unstamped
  // keys hit isClientOnlyResumeKey below).
  const resumeMessageId = resolveResumeMessageId(conversationId, messageId);

  // Capture the pending frame BEFORE removing it — sidecar path needs its
  // original user message text / pinned user bubble id; refuse path restores it.
  const pending = usePausedTurnStore
    .getState()
    .pending.find((p) => p.messageId === resumeMessageId);
  const origin = resolveResumeOrigin(conversationId, resumeMessageId);
  const viaSidecar = shouldResumeViaSidecar(origin);
  // origin=sidecar：跟本地事实，忽略显式强制关（勿 resolveSidecarRoot）。
  const sidecarTarget = viaSidecar
    ? await resolveResumeSidecarTarget(conversationId)
    : null;

  const raiseSidecarUnavailable = (detail: string | null) => {
    // Drop the bad-health cache so the next ResumePrompt submit re-probes
    // (banner no longer one-click retries).
    clearSidecarHealth();
    store.setError(
      detail
        ? `${detail}，本地引擎暂不可用，无法继续这次暂停的回合，请稍后重试`
        : "本地引擎暂不可用，无法继续这次暂停的回合，请稍后重试",
      null,
      conversationId,
      null,
    );
  };

  // A paused sidecar frame lives ONLY on this machine — never degrade to cloud
  // (cloud has no such frame → guaranteed 404). No local target → keep card + banner.
  // Throw so callers (submitInteraction) do not markResolved on a silent early exit.
  if (viaSidecar && !sidecarTarget) {
    raiseSidecarUnavailable(null);
    throw new Error("resume blocked: sidecar unavailable");
  }

  // Probe first: if the env can't start, keep the resume card and raise a
  // banner — never a guaranteed-404 cloud resume.
  if (viaSidecar && sidecarTarget) {
    const probe = await probeSidecar(sidecarTarget);
    if (!probe.healthy) {
      raiseSidecarUnavailable(probe.detail);
      throw new Error("resume blocked: sidecar probe failed");
    }
  }

  if (isClientOnlyResumeKey(conversationId, resumeMessageId)) {
    store.setError(
      "续跑键无效（缺少服务端消息 ID），无法继续这次暂停的回合，请稍后重试",
      null,
      conversationId,
      null,
    );
    throw new Error("resume blocked: client-only message id");
  }

  // Same-turn continuation: flip the paused assistant back to streaming.
  // Reload race: bubble may be missing → fall back to a fresh streaming slot.
  // D9 live 并存：禁止在 live 尾气泡上再造一条 streaming assistant（会抢走 content_delta）。
  const resumed = store.resumePausedAssistant(resumeMessageId, conversationId);
  if (!resumed && !liveGeneratingAtStart) {
    store.createAssistantMessage(conversationId);
    store.setServerMessageIdOnLastMessage(resumeMessageId, conversationId);
  }

  // Optimistic: drop the pausedTurns shell as the request fires. InteractionStore
  // cold pending stays in submitting via submitInteraction; restore shell only
  // when the request is refused before any stream opens (and the frame still exists).
  const pendingSnapshot = pending;
  const hadPausedFrame = pendingSnapshot != null;
  if (hadPausedFrame) {
    usePausedTurnStore.getState().remove(resumeMessageId);
  }

  const priorUser = [...getRuntime(conversationId).messages]
    .reverse()
    .find((m) => m.role === "user");
  const userMessage = pendingSnapshot?.userMessage || priorUser?.content || "";
  const userMessageId = pendingSnapshot?.userMessageId || priorUser?.id || "";

  const ac = new AbortController();
  store.setAbort(ac, conversationId);
  beginTurnPreflight(conversationId);
  try {
    if (viaSidecar && sidecarTarget) {
      await resumeConversationViaSidecar({
        conversationId,
        rootId: sidecarTarget.rootId,
        subpath: sidecarTarget.subpath,
        messageId: resumeMessageId,
        decision,
        note,
        selected,
        userMessage,
        userMessageId,
        signal: ac.signal,
      });
    } else {
      await resumeConversation({
        conversationId,
        messageId: resumeMessageId,
        decision,
        note,
        selected,
        signal: ac.signal,
      });
    }
  } catch (err) {
    if (isAbort(err)) {
      finalizeHonestStopAbort(conversationId);
      return;
    }
    // A mid-stream drop no longer means the turn died (1a: it runs detached) —
    // rejoin it live (1b) rather than re-resuming, which would double-run it.
    // Do NOT restore the resume card — the turn is already running server-side.
    if (isTransportDrop(err) && (await rejoinLiveTurn(conversationId))) {
      return;
    }
    // Transient refusal before any stream opened → put the shell back.
    // Frame-gone (404 / PAUSED_TURN_NOT_FOUND) → keep dropped (协议：帧不存在应丢卡).
    if (
      isResumeRequestRefused(err) &&
      hadPausedFrame &&
      pendingSnapshot &&
      !isPausedFrameGone(err)
    ) {
      usePausedTurnStore.getState().addLiveResume(pendingSnapshot);
    }
    const s = useConversationStore.getState();
    // D9：续跑失败不得收口本就在跑的 live 回合。
    if (!liveGeneratingAtStart && getRuntime(conversationId).isGenerating) {
      s.finalizeLastMessage(conversationId);
    }
    // A failed turn never delivers `approval_resolved`; drop this conversation's
    // paused prompt (other conversations keep theirs). Hot-only orphan — cold 卡保留。
    clearInteractionPrompts(conversationId);
    const msg = describeStreamError(err);
    if (msg) {
      s.setError(msg, null, conversationId, streamErrorAction(err));
    }
    // Re-throw so submitInteraction does not markResolved (假成功).
    throw err;
  } finally {
    // D9 deferred 下两次续跑可长时间并存（旧的一次被顶替后才退场）：只清自己登记的那个
    // controller，否则会把仍在等槽的那次续跑的停止句柄一并抹掉。
    if (getRuntime(conversationId).abort === ac) {
      useConversationStore.getState().setAbort(null, conversationId);
    }
  }
}
