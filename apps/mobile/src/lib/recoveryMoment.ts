import {
  recoveryMomentRecoveryClause,
  recoveryMomentResetClause,
} from "@agentcore/protocol-fold-kit";

/**
 * 把服务端下发的**结构化恢复时刻**渲染成用户本机时区的文案（429 / 平台配额闸门）。
 *
 * 服务端不再往文案里写时刻，只给 ISO8601 UTC 绝对时刻，文案退成不含时刻的兜底句。原因是
 * 它只能写死一个时区：线上写的是「8 月 14 日 16:00（UTC）」，而中国用户真正要等的是北京
 * 时间次日零点——照 UTC 读就会算错，回来再撞同一堵墙。
 *
 * 两条规则：
 * - 拿到时刻 → 保留服务端原句，只在后面追加本机时区的时刻子句。**不标时区名**：渲染
 *   出来的就是用户自己的钟，标了反倒像在说别人的时间。
 * - 拿不到（旧服务端、字段非法、冷加载的 `runs.error` 只有 code + message）→ **原样**
 *   转述服务端那句，绝不自己编一个时间。
 *
 * 不按错误码整句重写。成文函数（本文件）两端各写一份；子句已逐字一致，走 kit。
 */

/**
 * 错误上随行的结构化时刻。服务端同期落地，生成类型跟上之前先在此声明形状——两个字段都是
 * 「有就用、没有就闭嘴」，所以旧服务端的响应落到这里也只是全 `undefined`。
 */
export interface RecoveryMomentContext {
  /** 429 / QUOTA_EXCEEDED：上游额度恢复的绝对时刻（ISO8601 UTC）。 */
  recovery_at?: string | null;
  /** 平台配额闸门：配额窗口重置的绝对时刻（ISO8601 UTC）。 */
  reset_at?: string | null;
  /** 措辞分流：user = 用户自己的服务商额度；platform / 缺省 = 上游额度。 */
  credential_source?: string | null;
}

/**
 * ISO8601 时刻 → 本机时区的「8 月 15 日 00:00」；无值 / 非法输入返回 `null`。
 *
 * `timeZone` 只为测试注入一个确定的时区（生产永远走设备本地时区），因为「渲染的是本地时刻」
 * 这件事本身不能靠跑测试的机器碰巧在哪个时区来证明。
 */
export function formatLocalMoment(
  iso: string | null | undefined,
  timeZone?: string,
): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    ...(timeZone ? { timeZone } : {}),
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(at);
  const pick = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((p) => p.type === type)?.value ?? "";
  return `${pick("month")} 月 ${pick("day")} 日 ${pick("hour")}:${pick("minute")}`;
}

/**
 * 给一句服务端错误文案补上本机时区的时刻。没有可用时刻时原样返回。
 *
 * `recovery_at`（上游 429 / 平台额度撞墙）与 `reset_at`（平台配额闸门）同一姿势：保留
 * 原句，另起一句说时刻。配额闸门那句带着「已达每日 token 上限（1,234 / 5,000）」这类
 * 只有服务端知道的用量数字，客户端无从重写。
 *
 * 两个字段同时出现时以 `recovery_at` 为准：上游那堵墙比本地配额窗口更晚放行，说早的那个
 * 会让用户白跑一趟。
 *
 * `opts.code` 仍被调用方传入（errors / ChatPage / turnOutcome），成文不再按码分叉。
 */
export function withLocalRecoveryMoment(
  message: string,
  opts: {
    code?: string | null;
    context?: RecoveryMomentContext | null;
  },
): string {
  const context = opts.context;
  const recovery = formatLocalMoment(context?.recovery_at);
  const reset = recovery ? null : formatLocalMoment(context?.reset_at);
  const tail = recovery
    ? recoveryMomentRecoveryClause(recovery)
    : reset
      ? recoveryMomentResetClause(reset)
      : null;
  if (!tail) return message;
  const base = message.trimEnd();
  if (!base) return tail;
  return /[。；！？]$/.test(base) ? `${base}${tail}` : `${base}。${tail}`;
}
