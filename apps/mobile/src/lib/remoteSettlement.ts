/**
 * 「已由另一端处理」收口（云对话多端同权 B2 · P1 · 验收 5）。
 *
 * 同一张卡两端都能点，先到先得（定案 §3.2）。难点是 `*_resolved` 契约**不带处理方**——
 * 事件只说 settle 了，不说谁 settle 的。所以归属只能本端记账：发起收口前先登记 interaction
 * id，之后同 id 的收口就是自己人；没登记过的那次就是另一端点的。再加一道
 * {@link answeredByAPerson}——有些收口压根没有人参与（升级卡的主管仲裁 / 按假设 / 超时兜底）。
 *
 * 另一端点掉的卡**不能直接消失**——那会让用户以为是自己刚点的，也无从解释 AI 为什么自己
 * 往下跑了。改成就地收口成一张只读提示条，用户按「知道了」收走。
 *
 * 记账只在当前打开的对话内有意义（手机一次只看一个对话），切会话整体清空。
 */
import {
  INTERACTION_KIND_WIRE,
  type UserInteractionKind,
} from "@agentcore/contract-types";
import { interactionCardName } from "@agentcore/protocol-fold-kit";
import { useSyncExternalStore } from "react";

/** 收口文案（验收 5 的可见判据）。 */
export const REMOTE_SETTLED_TEXT = "已由另一端处理";

/** 一张被另一端点掉的卡留下的提示条。 */
export interface RemoteSettlement {
  interactionId: string;
  conversationId: string;
  /** 卡的种类——提示条标题由它取（{@link interactionLabel}），也是 REST 回执那道闸的判据。 */
  kind: string;
}

/** 收口事件名 → kind + id 字段（契约表反查；后端新增 kind 自动跟上）。 */
const BY_RESOLVED_EVENT = new Map<
  string,
  { kind: UserInteractionKind; idField: string }
>();
for (const [kind, wire] of Object.entries(INTERACTION_KIND_WIRE)) {
  const resolvedEvent = wire.resolvedEvent;
  if (!resolvedEvent) continue;
  BY_RESOLVED_EVENT.set(resolvedEvent, {
    kind: kind as UserInteractionKind,
    idField: wire.idField,
  });
}

export function interactionLabel(kind: string): string {
  return interactionCardName(kind);
}

/**
 * 这一帧的收口是**有人**答的吗？
 *
 * 「本端没登记过」离「另一端的人拍的」还差一步：升级卡还能由主管仲裁、或按假设推进 / 超时
 * 兜底收口——这类压根没有人参与，线材里 `status` 与 `arbitrated_by` 说得明明白白。要是也算
 * 到用户头上，就成了替他认领一个他没做过的动作，正是这张提示条要避免的误导。其余 kind 的
 * `*_resolved` 今天只有「人答了」这一个生产者（冷卡出自 resume 路，热审批出自决策路）。
 *
 * 判定口径与桌面 `answeredByAPerson` 一致（实现各端自建）。
 */
export function answeredByAPerson(
  kind: string,
  payload: Record<string, unknown> | undefined,
): boolean {
  if (kind !== "escalation") return true;
  // resolved 之外（assumed / timed_out / orphaned）都是运行时兜底，没有人参与。
  if (payload?.status !== "resolved") return false;
  // 经典用户直答路径缺省 arbitrated_by（契约原话），有值即走了仲裁通道；含 via_user——
  // 那种情况下人答的是主管的问，不是这张卡。
  return !payload.arbitrated_by;
}

/**
 * REST 回执（`already_processed` / 404）能证明是**人**结的吗？
 *
 * 回执只说「已经结了」，不带 `status` / `arbitrated_by`，所以对升级卡一律证不了——撞上主管
 * 仲裁或超时兜底时无从分辨。这类只能把归属交回带线材字段的 `*_resolved` 帧去判。
 */
export function receiptProvesAPerson(kind: string): boolean {
  return kind !== "escalation";
}

/**
 * 认收口事件并取出它结掉的是哪张卡；非收口事件 / 缺 id / **无人参与的收口**返回 null。
 */
export function settlementFromResolvedEvent(
  eventType: string,
  payload: Record<string, unknown> | undefined,
): { kind: UserInteractionKind; interactionId: string } | null {
  const wire = BY_RESOLVED_EVENT.get(eventType);
  if (!wire) return null;
  const raw = payload?.[wire.idField];
  if (typeof raw !== "string" || !raw) return null;
  if (!answeredByAPerson(wire.kind, payload)) return null;
  return { kind: wire.kind, interactionId: raw };
}

type Listener = () => void;

const localSettlements = new Set<string>();
let entries: readonly RemoteSettlement[] = [];
const listeners = new Set<Listener>();

function emit(): void {
  for (const l of listeners) l();
}

/** 本端要点这张卡了——**必须在 POST 之前**登记，否则抢先回来的收口事件会被当成外来的。 */
export function markLocalSettlement(interactionId: string): void {
  if (interactionId) localSettlements.add(interactionId);
}

export function isLocalSettlement(interactionId: string): boolean {
  return localSettlements.has(interactionId);
}

/**
 * 撤回登记：本端这一点并没有结掉它（回执 `already_processed`）。
 *
 * 不撤的话，随后那帧带线材字段的 `*_resolved` 会被当成自己人而放过——恰恰它才是唯一能分清
 * 「另一端的人拍的」与「主管仲裁 / 超时兜底」的证据。
 */
export function unmarkLocalSettlement(interactionId: string): void {
  localSettlements.delete(interactionId);
}

/**
 * 这次收口该不该记成「另一端处理」= 本端没登记过 **且** 卡此刻正摆在用户面前。
 *
 * 「正摆着」这道闸同时挡住重放误报：重放段里 `*_required` 与 `*_resolved` 同一批同步到达，
 * 卡一次都没露过面，自然不在可见集合里——历史上每次放行都弹一张墓碑才是灾难。
 */
export function isForeignSettlement(
  interactionId: string,
  visibleCardIds: ReadonlySet<string>,
): boolean {
  if (!interactionId) return false;
  if (localSettlements.has(interactionId)) return false;
  return visibleCardIds.has(interactionId);
}

/**
 * 记一张「已由另一端处理」提示条（同 id 幂等：事件与 REST 回执可能各报一次）。
 *
 * 走这个入口 = 归属已被证过（线材帧经 {@link settlementFromResolvedEvent}）。REST 回执请走
 * {@link noteRemoteSettlementFromReceipt}，它自带「回执证不了人」那道闸。
 */
export function noteRemoteSettlement(entry: RemoteSettlement): void {
  if (!entry.interactionId || !entry.conversationId) return;
  if (entries.some((e) => e.interactionId === entry.interactionId)) return;
  entries = [...entries, entry];
  emit();
}

/**
 * REST 回执路径的入口：证不了人就不立提示条，返回 false 交给调用方另作交代。
 *
 * 集中把闸放在这里，而不是散在各卡里——将来多一个回执调用点也不会漏掉这道判断。
 */
export function noteRemoteSettlementFromReceipt(
  entry: RemoteSettlement,
): boolean {
  if (!receiptProvesAPerson(entry.kind)) return false;
  noteRemoteSettlement(entry);
  return true;
}

export function dismissRemoteSettlement(interactionId: string): void {
  const next = entries.filter((e) => e.interactionId !== interactionId);
  if (next.length === entries.length) return;
  entries = next;
  emit();
}

/** 切会话 / 登出：提示条与记账一起作废。 */
export function resetRemoteSettlements(): void {
  localSettlements.clear();
  if (entries.length === 0) return;
  entries = [];
  emit();
}

export function getRemoteSettlementSnapshot(): readonly RemoteSettlement[] {
  return entries;
}

export function subscribeRemoteSettlements(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const EMPTY: readonly RemoteSettlement[] = [];

/** React 订阅：本对话的「已由另一端处理」提示条。 */
export function useRemoteSettlements(
  conversationId: string | null | undefined,
): readonly RemoteSettlement[] {
  const snap = useSyncExternalStore(
    subscribeRemoteSettlements,
    getRemoteSettlementSnapshot,
    getRemoteSettlementSnapshot,
  );
  if (!conversationId) return EMPTY;
  return snap.filter((e) => e.conversationId === conversationId);
}

export function __resetRemoteSettlementsForTests(): void {
  resetRemoteSettlements();
}
