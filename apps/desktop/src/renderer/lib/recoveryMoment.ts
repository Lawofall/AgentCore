import {
  recoveryMomentRecoveryClause,
  recoveryMomentResetClause,
} from "@agentcore/protocol-fold-kit";

/**
 * 服务端下发的绝对恢复时刻（ISO8601 UTC）→ 用户本机时区的话。
 *
 * 成文权在客户端。服务端曾把措辞好的「8 月 14 日 16:00（UTC）」直接写进句子，中国用户
 * 照字面等到当天下午四点，真正能用的却是北京时间次日零点——同一句话把人骗去白等一整天。
 * 现在服务端只给瞬间，句子里的时刻由各端按本机时区成文（三端同格式）。
 *
 * **不标时区名**：渲染出来的就是用户本机的钟，补一个「(UTC+8)」只会让人怀疑这个时刻说的
 * 是不是别人的时区。解析不出来（缺字段、坏字符串）就当没有——退回服务端那句不含时刻的
 * 兜底，绝不自己编一个时间。
 */

/** 三端统一的本机时刻格式「8 月 15 日 00:00」；无法解析时返回 null。 */
export function formatLocalMoment(
  at: string | null | undefined,
): string | null {
  if (!at) return null;
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${pad(d.getHours())}:${pad(
    d.getMinutes(),
  )}`;
}

/**
 * 错误上的结构化时刻：上游 429 给 `recovery_at`（额度恢复），平台配额闸门给 `reset_at`
 * （配额重置）。两条传输各放一处——REST 挂在 `error` 上，SSE 挂在 `error.context` 里。
 */
export interface RecoveryMomentFields {
  recovery_at?: string | null;
  reset_at?: string | null;
  context?: {
    recovery_at?: string | null;
    reset_at?: string | null;
  } | null;
}

/** 时刻子句「额度将于 8 月 15 日 00:00 恢复。」；没有可用时刻时返回 null。 */
export function recoveryMomentClause(
  source: RecoveryMomentFields | null | undefined,
): string | null {
  if (!source) return null;
  const recovery = formatLocalMoment(
    source.recovery_at ?? source.context?.recovery_at,
  );
  if (recovery) return recoveryMomentRecoveryClause(recovery);
  const reset = formatLocalMoment(source.reset_at ?? source.context?.reset_at);
  if (reset) return recoveryMomentResetClause(reset);
  return null;
}

/**
 * 服务端兜底句 + 本机时刻子句。语气跟着服务端那句走，拿到时刻只多说出时刻本身；
 * 拿不到就原样返回服务端那句。
 */
export function withRecoveryMoment(
  sentence: string,
  source: RecoveryMomentFields | null | undefined,
): string {
  const clause = recoveryMomentClause(source);
  if (!clause) return sentence;
  const base = sentence.trim();
  if (!base) return clause;
  return /[。！？；]$/.test(base) ? `${base}${clause}` : `${base}。${clause}`;
}
