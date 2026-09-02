import type { HandoffJob } from "@/services/handoff";
import type { BrowserTakeover } from "@/stores/browserTakeover";
import type { MemoryUpdate, Message } from "@/stores/conversation";
import type { PermissionChange } from "@/stores/permissionChanges";

export type TimelineItem =
  | { kind: "message"; at: number; key: string; msg: Message }
  | { kind: "task"; at: number; key: string; job: HandoffJob }
  | { kind: "memory"; at: number; key: string; update: MemoryUpdate }
  | { kind: "takeover"; at: number; key: string; takeover: BrowserTakeover }
  | {
      kind: "preset-change";
      at: number;
      key: string;
      change: PermissionChange;
    }
  | { kind: "compaction"; at: number; key: string };

// Same-timestamp ordering. message/task form the base timeline (a turn's message first,
// then any background task it spawned). memory + takeover + preset-change are NOT ordered
// here — they snap to exchange boundaries below, not to a raw timestamp slot; the order
// value only breaks ties among two anchored cards landing on the same boundary.
const KIND_ORDER: Record<TimelineItem["kind"], number> = {
  message: 0,
  task: 1,
  memory: 2,
  takeover: 3,
  "preset-change": 4,
  compaction: 5,
};

/**
 * 记忆卡的锚定时刻：后端给的 `anchor_at`（本次固化窗口最后一条消息 = 被总结那一轮的末尾）
 * 优先。live 语义卡也会带锚点，不只是已删的摘记卡；缺省（老数据）回落到落库时刻 `createdAt`。
 *
 * 卡片右上角展示的时间戳也走这里：既然卡片是按这个时刻插进时间线的，展示落库时刻会让卡片
 * 的时间比它下方那条消息还晚，读起来是乱序。
 */
export const memoryAnchorTime = (update: MemoryUpdate): string =>
  update.anchorAt ?? update.createdAt;

/**
 * 把消息、后台云端任务、记忆更新卡并成一条时间线。
 *
 * 消息 + 任务按 `created_at` 排成「基准时间线」；记忆卡则**锚定到它所在那一回合的末尾**
 * ——AI 回答完成之后、下一次提问之前——而非按裸时间戳就地插。原因：offline-consolidation
 * 是回合结束后异步跑的（略滞后），而助手消息落库用的是「回合完成」时刻的时间戳；裸时间戳
 * 排序会让一张滞后的记忆卡正好落在「新提问 ↔ 长回合回答」之间，被夹进问答对里。锚到回合
 * 末尾既不打断问答对，又让每回合各一张、按时间分布，不会退回「全堆在对话最底部」的老毛病
 * （记忆更新对话内可见 §1.6）。无任务且无记忆卡时退化为纯消息列表（最常见路径）。
 *
 * 记忆卡用 {@link memoryAnchorTime} 而非落库时刻定位：固化滞后常常超过用户发下一条消息的
 * 间隔，此时落库时刻已晚于「下一条提问」，卡片会落空锚点冲到列表末尾。
 */
export function mergeTimeline(
  messages: Message[],
  tasks: HandoffJob[],
  memoryUpdates: MemoryUpdate[] = [],
  takeovers: BrowserTakeover[] = [],
  presetChanges: PermissionChange[] = [],
  compactedThrough?: string | null,
): TimelineItem[] {
  const anchoredCount =
    memoryUpdates.length + takeovers.length + presetChanges.length;
  if (tasks.length === 0 && anchoredCount === 0) {
    return insertCompactionDivider(
      messages.map((msg) => ({
        kind: "message" as const,
        at: Date.parse(msg.createdAt) || 0,
        key: `m:${msg.id}`,
        msg,
      })),
      compactedThrough,
    );
  }

  const base: TimelineItem[] = [
    ...messages.map(
      (msg): TimelineItem => ({
        kind: "message",
        at: Date.parse(msg.createdAt) || 0,
        key: `m:${msg.id}`,
        msg,
      }),
    ),
    ...tasks.map(
      (job): TimelineItem => ({
        kind: "task",
        at: Date.parse(job.createdAt) || 0,
        key: `t:${job.id}`,
        job,
      }),
    ),
  ];
  base.sort((a, b) => a.at - b.at || KIND_ORDER[a.kind] - KIND_ORDER[b.kind]);

  if (anchoredCount === 0) {
    return insertCompactionDivider(base, compactedThrough);
  }

  // Anchored cards (memory 记忆卡 + takeover 接管标记卡 + preset-change 权限模式切换系统行),
  // oldest-first, each dropped just before the NEXT user message that starts after it (= the
  // end of the exchange active at its timestamp), or at the very tail when no later turn
  // exists. A user message is the only exchange boundary; assistant replies / tasks belong to
  // that exchange, so a card always lands after them. Takeovers anchor on their START time —
  // D16 only allows takeover between turns / after an ask_user pause. A preset switch「下一回合
  // 生效」so anchoring before the next user message puts the「权限模式 A → B」line right ahead of
  // the turn it governs.
  const anchored: TimelineItem[] = [
    ...memoryUpdates.map(
      (update): TimelineItem => ({
        kind: "memory",
        at: Date.parse(memoryAnchorTime(update)) || 0,
        key: `mem:${update.id}`,
        update,
      }),
    ),
    ...takeovers.map(
      (takeover): TimelineItem => ({
        kind: "takeover",
        at: Date.parse(takeover.startedAt) || 0,
        key: `tko:${takeover.id}`,
        takeover,
      }),
    ),
    ...presetChanges.map(
      (change): TimelineItem => ({
        kind: "preset-change",
        at: Date.parse(change.at) || 0,
        key: `pc:${change.id}`,
        change,
      }),
    ),
  ].sort((a, b) => a.at - b.at || KIND_ORDER[a.kind] - KIND_ORDER[b.kind]);

  const result: TimelineItem[] = [];
  let ai = 0;
  for (const item of base) {
    if (item.kind === "message" && item.msg.role === "user") {
      while (ai < anchored.length && anchored[ai].at < item.at) {
        result.push(anchored[ai++]);
      }
    }
    result.push(item);
  }
  while (ai < anchored.length) result.push(anchored[ai++]);

  return insertCompactionDivider(result, compactedThrough);
}

/**
 * Fold boundary sits after the last loaded message whose ``created_at`` is still
 * at/before ``compacted_through`` (last folded row). Loaded window entirely after
 * the watermark → no divider (boundary is in not-yet-loaded older turns).
 */
function insertCompactionDivider(
  items: TimelineItem[],
  compactedThrough?: string | null,
): TimelineItem[] {
  if (!compactedThrough) return items;
  const at = Date.parse(compactedThrough);
  if (!Number.isFinite(at)) return items;

  let insertAfter = -1;
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.kind === "message" && item.at <= at) insertAfter = i;
  }
  if (insertAfter < 0) return items;

  const divider: TimelineItem = { kind: "compaction", at, key: "compaction" };
  return [
    ...items.slice(0, insertAfter + 1),
    divider,
    ...items.slice(insertAfter + 1),
  ];
}
