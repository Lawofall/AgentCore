/**
 * 恢复 / 重置时刻子句 —— 两端已经逐字对齐的那两句。
 *
 * 成文函数（`formatLocalMoment` / `withRecoveryMoment`）不进本文件：各端拼原句的
 * 姿势可以相同，但时刻格式化实现并不逐字一致（手机可注入 timeZone 供测试钉死）。
 */

/** 「额度将于 8 月 15 日 00:00 恢复。」 */
export function recoveryMomentRecoveryClause(localMoment: string): string {
  return `额度将于 ${localMoment} 恢复。`;
}

/** 「额度将于 8 月 15 日 00:00 重置。」 */
export function recoveryMomentResetClause(localMoment: string): string {
  return `额度将于 ${localMoment} 重置。`;
}
