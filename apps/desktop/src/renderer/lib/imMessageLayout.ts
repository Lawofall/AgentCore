import { formatDateDivider } from "@/lib/format";
import type { ChatMessageDetail } from "@/services/messaging";

/** Same sender within this gap (ms) merges into one visual cluster. */
export const IM_CLUSTER_GAP_MS = 5 * 60 * 1000;

/**
 * Desktop IM session column (messages + composer). 832px — desktop IM, not a
 * phone strip. Wider than the AI 对话页 reading column (`max-w-3xl` / 768px)
 * because left/right bubbles need the extra room; still capped so ultrawide
 * panes don't go edge-to-edge. Header stays full-pane.
 */
export const IM_SESSION_COLUMN_CLASS = "mx-auto w-full min-w-0 max-w-[52rem]";

/** Bubble cap inside the session column (WhatsApp / Telegram). */
export const IM_BUBBLE_MAX_CLASS = "max-w-[75%]";

export type ImClusterPosition = "single" | "first" | "middle" | "last";

export interface ImBubbleLayout {
  clusterPosition: ImClusterPosition;
  showAvatar: boolean;
  showSenderName: boolean;
  /** Tighten top spacing when continuing a cluster. */
  tightTop: boolean;
}

export type ImThreadItem =
  | { type: "date_divider"; label: string; key: string }
  | {
      type: "message";
      message: ChatMessageDetail;
      layout: ImBubbleLayout;
      key: string;
    };

function sameCalendarDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function dayKey(iso: string): string | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function isClusterable(msg: ChatMessageDetail): boolean {
  if (msg.recalled_at) return false;
  return msg.content_type !== "system_card";
}

function clustersWith(
  earlier: ChatMessageDetail,
  later: ChatMessageDetail,
): boolean {
  if (!isClusterable(earlier) || !isClusterable(later)) return false;
  if (earlier.sender_user_id !== later.sender_user_id) return false;
  const t0 = new Date(earlier.created_at).getTime();
  const t1 = new Date(later.created_at).getTime();
  if (Number.isNaN(t0) || Number.isNaN(t1)) return false;
  return t1 - t0 >= 0 && t1 - t0 < IM_CLUSTER_GAP_MS;
}

export function computeBubbleLayout(
  messages: readonly ChatMessageDetail[],
  index: number,
): ImBubbleLayout {
  const msg = messages[index];
  if (!isClusterable(msg)) {
    return {
      clusterPosition: "single",
      showAvatar: true,
      showSenderName: true,
      tightTop: false,
    };
  }

  const prev = index > 0 ? messages[index - 1] : null;
  const next = index < messages.length - 1 ? messages[index + 1] : null;
  const withPrev = prev ? clustersWith(prev, msg) : false;
  const withNext = next ? clustersWith(msg, next) : false;

  let clusterPosition: ImClusterPosition;
  if (!withPrev && !withNext) clusterPosition = "single";
  else if (withPrev && withNext) clusterPosition = "middle";
  else if (!withPrev && withNext) clusterPosition = "first";
  else clusterPosition = "last";

  return {
    clusterPosition,
    showAvatar: !withPrev,
    showSenderName: !withPrev,
    tightTop: withPrev,
  };
}

/** Oldest-first messages → thread rows with date pills and per-bubble layout hints. */
export function buildImThreadItems(
  messages: readonly ChatMessageDetail[],
): ImThreadItem[] {
  const items: ImThreadItem[] = [];
  let lastDay: string | null = null;

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    const dk = dayKey(msg.created_at);
    if (dk && dk !== lastDay) {
      const label = formatDateDivider(msg.created_at);
      if (label) {
        items.push({ type: "date_divider", label, key: `date-${dk}` });
      }
      lastDay = dk;
    }
    items.push({
      type: "message",
      message: msg,
      layout: computeBubbleLayout(messages, i),
      key: msg.id,
    });
  }

  return items;
}

export { sameCalendarDay };
