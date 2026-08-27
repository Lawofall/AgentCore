import { getConversations } from "@/hooks/useConversations";
import { showNativeNotification } from "@/lib/nativeNotification";
import { queryClient } from "@/lib/queryClient";
import { conversationKeys } from "@/lib/queryKeys";
import {
  conversationIdFromHash,
  isTransientRoute,
  runtimeHasError,
} from "@/lib/teamActivity";
import { notifyInfo } from "@/lib/toast";
import {
  type AiAttentionEntry,
  useAiAttentionStore,
} from "@/stores/aiAttention";
import {
  ignoresCloudTurnActivity,
  useAiTurnActivityStore,
} from "@/stores/aiTurnActivity";
import { DRAFT_KEY, useConversationStore } from "@/stores/conversation";
import {
  type InteractionEntry,
  isAwaitingUserEntry,
  isColdResumeKind,
  isStageInteractionKind,
  useInteractionStore,
} from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";

/**
 * 跨对话完成通知 (前端UX设计.md §一 全局协作感知)：只读订阅对话生成态 + 交互态 + 挂起态，当用户**不在**某对话
 * 页面时，该对话的关键事件（回合完成 / 失败 / 等你拍板 / 挂起等确认）弹一条带「跳转」action 的 notifyInfo。
 * 纯前端感知层——不碰 SSE 契约 / 协议 fold，不新增事件；接线一次于 AppShell（与 realtime /
 * updates 同处），随会话常驻。
 *
 * 云对话完成认 fulfill `ai_turn_activity` 的 `reason`：只对 `completed|error` 报完成/
 * 失败，`paused|stopped` 不报「已完成」。本机 sidecar / 本地容器忽略云信号，仍走本端
 * isGenerating↓。挂起收口（可操作 cold resume 仍在）也不是「已完成」，感知统一走
 * pausedTurns 订阅（ask_user / plan_review → 等待确认后继续）。
 *
 * 热阻塞卡（approval / escalation）走 InteractionStore 订阅，
 * 判定直接复用侧栏「等你」灯的 {@link isAwaitingUserEntry}（含 CEO 仲裁中的升级卡不打扰）
 * ——挂起时回合仍在 streaming，「已完成」通道不会触发，后端也默认无限期等，这里不提醒
 * 就只剩侧栏一颗小圆点。
 *
 * 幕终推进卡（stage_card pending，非冷挂起）：不阻塞执行，所以不在「等你」判定里，但
 * 完成通道同样跳过「已完成」，感知也走 InteractionStore 订阅（「需要你确认推进」）。
 *
 * 以上三条通道只看得见**本端流过**的对话。第四条 `ai_attention`（云对话多端同权 B2 · L1）
 * 补上另一端起的回合：账号级 firehose 送「哪个对话在等你」，因此从没在这台机器上打开过的
 * 对话也能提醒。四条通道共用一张去重表（信号里的 `interaction_id` 与卡的 id 同一个），
 * 谁先到谁弹，另一条不重复打扰。
 */

/** 从会话缓存解析标题（非 React 调用）——缺（未加载 / 已删）时返回 null，调用方据此静默。 */
function titleOf(id: string): string | null {
  return getConversations().find((c) => c.id === id)?.title ?? null;
}

/** 跳转到某对话：先同步切当前会话（即时反馈），再驱动 hash 路由（等同点 <Link>）。 */
function jumpTo(conversationId: string): void {
  useConversationStore.getState().switchConversation(conversationId);
  window.location.hash = `/conversations/${conversationId}`;
}

/** 该对话不是当前正看的、也不在开发回放态 → 值得弹通知。 */
function shouldNotify(conversationId: string): boolean {
  const hash = window.location.hash;
  if (isTransientRoute(hash)) return false;
  return conversationIdFromHash(hash) !== conversationId;
}

function notifyTurnEnd(conversationId: string, failed: boolean): void {
  if (!shouldNotify(conversationId)) return;
  const title = titleOf(conversationId);
  if (!title) return;
  const message = failed ? `「${title}」执行失败` : `「${title}」已完成`;
  notifyInfo(message, {
    action: { label: "查看", onClick: () => jumpTo(conversationId) },
  });
  void showNativeNotification("AgentCore", message, { conversationId });
}

/**
 * 热阻塞卡的一句话文案。三类都把回合钉在用户身上，措辞跟服务端 `attention_title`
 * 的 per-kind headline 对齐（`agentcore/attention/signal.py`），免得同一张卡在
 * firehose 与本端两路提醒里说法不一。
 */
const HOT_BLOCKING_HEADLINE: Partial<Record<InteractionEntry["kind"], string>> =
  {
    approval: "需要审批",
    escalation: "需要你的决定",
  };

function notifyHotBlocking(entry: InteractionEntry): void {
  const headline = HOT_BLOCKING_HEADLINE[entry.kind];
  if (!headline) return;
  const conversationId = entry.conversationId;
  if (!shouldNotify(conversationId)) return;
  const title = titleOf(conversationId);
  if (!title) return;
  const message = `「${title}」${headline}`;
  notifyInfo(message, {
    action: { label: "去处理", onClick: () => jumpTo(conversationId) },
  });
  void showNativeNotification("AgentCore", message, { conversationId });
}

/** ask_user / plan_review 挂起：文案区分「开工」vs「拍板」。 */
function notifyAwaitingDecision(conversationId: string): void {
  if (!shouldNotify(conversationId)) return;
  const title = titleOf(conversationId);
  if (!title) return;
  const message = `「${title}」等待你确认后才会继续`;
  notifyInfo(message, {
    action: { label: "去处理", onClick: () => jumpTo(conversationId) },
  });
  void showNativeNotification("AgentCore", message, { conversationId });
}

/** 幕终阶段推进卡：对齐 approval / kickoff 通道。 */
function notifyStageAdvance(conversationId: string): void {
  if (!shouldNotify(conversationId)) return;
  const title = titleOf(conversationId);
  if (!title) return;
  const message = `「${title}」需要你确认推进`;
  notifyInfo(message, {
    action: { label: "去处理", onClick: () => jumpTo(conversationId) },
  });
  void showNativeNotification("AgentCore", message, { conversationId });
}

/**
 * firehose「某个对话在等你」：卡在另一端起的回合上，本端可能连这个对话都没加载过。
 *
 * 与上面几条不同，**标题缺失不静默**——「找得到人」正是这条信号的全部意义；缺标题只说明
 * 会话列表还没刷到这条（例如手机上刚建的对话），此时用信号自带的一行标题顶上，并顺手让
 * 列表失效，侧栏拿到行之后「等你」灯才有地方亮。
 */
function notifyAttention(entry: AiAttentionEntry): void {
  if (!shouldNotify(entry.conversationId)) return;
  const title = titleOf(entry.conversationId);
  if (!title) {
    void queryClient.invalidateQueries({ queryKey: conversationKeys.grouped });
  }
  const headline = entry.title.trim() || "AI 停下来等你处理";
  const message = title ? `「${title}」· ${headline}` : headline;
  notifyInfo(message, {
    action: {
      label: "去处理",
      onClick: () => jumpTo(entry.conversationId),
    },
  });
  void showNativeNotification("AgentCore", message, {
    conversationId: entry.conversationId,
  });
}

/**
 * 这张卡该不该由交互通道提醒——订阅端与去重表 seed 共用同一判定，避免两边漏配。
 *
 * 热阻塞卡走「等你」语义（{@link isAwaitingUserEntry}）；stage_card 不阻塞执行，
 * 单列一支。其余（冷挂起 / 非阻塞提问）另有通道。
 */
function isNotifiableInteraction(e: InteractionEntry): boolean {
  if (isStageInteractionKind(e.kind)) {
    return e.status === "pending" || e.status === "submitting";
  }
  return isAwaitingUserEntry(e);
}

function notifiableInteractionIds(): string[] {
  const out: string[] = [];
  for (const e of useInteractionStore.getState().byId.values()) {
    if (isNotifiableInteraction(e)) out.push(e.id);
  }
  return out;
}

/** All durable pause frames (any SuspensionKind) — seed + live dedup keys. */
function pendingPauseIds(): string[] {
  return usePausedTurnStore.getState().pending.map((p) => p.checkpointId);
}

/** firehose 侧仍在等的卡（`interaction_id` 与本地卡 id 同源，可同表去重）。 */
function attentionIds(): string[] {
  return useAiAttentionStore.getState().entries.map((e) => e.interactionId);
}

/** 四条通道当前仍「在等」的全部 id——去重表照它收敛，不会无界增长。 */
function liveNotifiableIds(): Set<string> {
  return new Set([
    ...notifiableInteractionIds(),
    ...pendingPauseIds(),
    ...attentionIds(),
  ]);
}

function ignoresCloudActivity(conversationId: string): boolean {
  const via =
    useConversationStore.getState().byId[conversationId]?.executionVia ?? null;
  const localContainerRootId =
    getConversations().find((c) => c.id === conversationId)
      ?.localContainerRootId ?? null;
  return ignoresCloudTurnActivity(via, localContainerRootId);
}

function conversationHasPausedTurn(conversationId: string): boolean {
  return usePausedTurnStore
    .getState()
    .pending.some(
      (p) => p.conversationId === conversationId && isColdResumeKind(p.kind),
    );
}

function conversationHasPendingStageCard(conversationId: string): boolean {
  for (const e of useInteractionStore.getState().byId.values()) {
    if (
      e.conversationId === conversationId &&
      isStageInteractionKind(e.kind) &&
      (e.status === "pending" || e.status === "submitting")
    ) {
      return true;
    }
  }
  return false;
}

/**
 * Start the ambient cross-conversation notifier. Returns an unsubscribe fn (AppShell
 * calls it on unmount). Idempotent per call — each invocation owns its own subscriptions.
 */
export function startTeamActivityNotifications(): () => void {
  // Seed with hot blocking cards / stage cards / pauses / attention already pending
  // at startup so a reconnect replay doesn't re-toast prompts the user already knows
  // about. 一张表跨四条通道：同一张卡从 firehose 与对话流两路到达只弹一次。
  const notified = liveNotifiableIds();
  const prune = (): void => {
    const live = liveNotifiableIds();
    for (const seen of notified) {
      if (!live.has(seen)) notified.delete(seen);
    }
  };
  /** 未通知过 → 记账并返回 true（调用方随即弹）。 */
  const claim = (id: string): boolean => {
    if (notified.has(id)) return false;
    notified.add(id);
    return true;
  };

  const unsubConversation = useConversationStore.subscribe((state, prev) => {
    for (const [id, prevRt] of Object.entries(prev.byId)) {
      if (id === DRAFT_KEY || !prevRt.isGenerating) continue;
      const nextRt = state.byId[id];
      if (nextRt?.isGenerating) continue; // still streaming — not a turn boundary
      const failedAtBoundary = nextRt
        ? runtimeHasError(nextRt)
        : runtimeHasError(prevRt);
      queueMicrotask(() => {
        // 云对话完成认 `ai_turn_activity.reason`，本通道只服务 sidecar / 本地容器，
        // 避免同一收口被云信号与本端 isGenerating↓ 各弹一次。
        if (!ignoresCloudActivity(id)) return;
        // Durable pause close lands pausedTurns in the same sync turn as
        // finalizeLastMessage; by this microtask the frame is already there.
        // Skip「已完成」— pause perception is the pausedTurns channel only.
        if (conversationHasPausedTurn(id)) return;
        // 幕终推进卡：turn 正常收口但仍待用户确认推进——勿误报「已完成」。
        if (conversationHasPendingStageCard(id)) return;
        const latest = useConversationStore.getState().byId[id];
        const failed = latest ? runtimeHasError(latest) : failedAtBoundary;
        notifyTurnEnd(id, failed);
      });
    }
  });

  const unsubActivity = useAiTurnActivityStore.subscribe((state, prev) => {
    const done = state.lastDone;
    if (!done || done.seq === prev.lastDone?.seq) return;
    if (ignoresCloudActivity(done.conversationId)) return;
    if (done.reason === "paused" || done.reason === "stopped") return;
    if (done.reason === "completed" || done.reason === "error") {
      notifyTurnEnd(done.conversationId, done.reason === "error");
    }
  });

  const unsubInteractions = useInteractionStore.subscribe((state) => {
    for (const e of state.byId.values()) {
      if (!isNotifiableInteraction(e)) continue;
      if (!claim(e.id)) continue;
      if (isStageInteractionKind(e.kind)) {
        notifyStageAdvance(e.conversationId);
      } else {
        notifyHotBlocking(e);
      }
    }
    prune();
  });

  const unsubPaused = usePausedTurnStore.subscribe((state) => {
    for (const p of state.pending) {
      if (!isColdResumeKind(p.kind)) continue;
      if (!claim(p.checkpointId)) continue;
      notifyAwaitingDecision(p.conversationId);
    }
    prune();
  });

  const unsubAttention = useAiAttentionStore.subscribe((state) => {
    for (const entry of state.entries) {
      if (claim(entry.interactionId)) notifyAttention(entry);
    }
    prune();
  });

  return () => {
    unsubConversation();
    unsubActivity();
    unsubInteractions();
    unsubPaused();
    unsubAttention();
  };
}

/** OS 通知点击 → 跳转到对应对话（与 toast action 同路由）。 */
export function startNativeNotificationRouting(): () => void {
  const api =
    typeof window !== "undefined" ? window.notificationApi : undefined;
  if (!api?.onClicked) return () => {};
  return api.onClicked(({ conversationId }) => {
    if (conversationId) jumpTo(conversationId);
  });
}
