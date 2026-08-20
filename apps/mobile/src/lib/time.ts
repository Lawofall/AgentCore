import {
  type TimedWireEvent,
  isRunFrameEvent,
} from "@agentcore/protocol-fold-kit";

// Small date formatters for list rows + message timestamps (人际消息 / 对话列表).

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** "HH:MM" clock for a message timestamp. */
export function clock(iso: string): string {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * 消息时间戳展示串：今天 "HH:MM"，昨天 "昨天 HH:MM"，同年 "M月D日 HH:MM"，
 * 跨年带年。非法输入返回空串。
 */
export function formatMessageTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const tod = clock(iso);
  const now = new Date();
  const startOfDay = (x: Date) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000);
  if (days <= 0) return tod;
  if (days === 1) return `昨天 ${tod}`;
  const md = `${d.getMonth() + 1}月${d.getDate()}日`;
  if (d.getFullYear() === now.getFullYear()) return `${md} ${tod}`;
  return `${d.getFullYear()}年${md} ${tod}`;
}

/** First collab-frame event timestamp (epoch ms). Live strip ticks from this
 *  wall-clock anchor — `turnElapsedMs` span freezes while a long tool emits no frames. */
export function firstCollabAtMs(
  events: readonly TimedWireEvent[],
): number | null {
  for (const ev of events) {
    if (!isRunFrameEvent(ev.type)) continue;
    const t = ev.timestamp ? Date.parse(ev.timestamp) : Number.NaN;
    if (!Number.isNaN(t)) return t;
  }
  return null;
}

/** 毫秒时长 → "45s" / "2m34s" / "1h2m"（对齐桌面 formatDuration）。 */
export function formatDuration(ms: number): string {
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h${m}m`;
  return `${m}m${s}s`;
}

/** Compact relative label for a list row's last-activity time: clock today, 昨天
 *  yesterday, M月D日 within the year, else YYYY/M/D. */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const startOfDay = (x: Date) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000);
  if (days <= 0) return clock(iso);
  if (days === 1) return "昨天";
  if (d.getFullYear() === now.getFullYear())
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}
