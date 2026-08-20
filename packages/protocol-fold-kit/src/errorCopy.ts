/**
 * 空响应 / 空失败脸的协议文案 —— 诊断 chip 与四句 finish 兜底。
 *
 * 只收两端已经逐字对齐的映射。`describeError` / `errorActionForCode` /
 * 会话连通性计数 / StreamError 类是各端实现核，不进本文件。
 */

/** 降级空响应的短诊断（对齐后端 stamp；旧 journal 的 oauth_expired 同面）。 */
export const EMPTY_RESPONSE_CHIP_LABELS: Readonly<Record<string, string>> = {
  upstream_non_api: "上游返回了网页或登录页，请检查服务商地址与鉴权",
  oauth_expired: "上游返回了网页或登录页，请检查服务商地址与鉴权",
  content_filtered: "内容被过滤",
  model_unknown: "模型名未被上游识别",
  silent_empty: "模型返回空内容",
  format_mismatch: "上游响应格式异常",
  length_empty: "输出长度截断 · 返回空内容",
};

/** 助手泡已占用空响应红卡时，FinishReasonChip 不得再叠一层。 */
export function isEmptyResponseUserSurface(opts: {
  code?: string | null;
  emptyDiagnosis?: string | null;
  message?: string | null;
}): boolean {
  if (opts.code === "LLM_EMPTY_RESPONSE") return true;
  if (opts.emptyDiagnosis) return true;
  const msg = opts.message ?? "";
  return msg.includes("模型多次空响应") || msg.includes("模型空响应");
}

/** 有诊断码走表；否则从「主句 · 后缀」里取后缀。 */
export function degradedFinishChipLabel(
  diagnosis: string | undefined,
  errorMessage: string | undefined,
): string | undefined {
  if (diagnosis && EMPTY_RESPONSE_CHIP_LABELS[diagnosis]) {
    return EMPTY_RESPONSE_CHIP_LABELS[diagnosis];
  }
  if (errorMessage?.includes(" · ")) {
    return errorMessage.split(" · ", 2)[1];
  }
  return undefined;
}

/** `finish_reason=error` / `LLM_ERROR` 空失败脸。 */
export const LLM_ERROR_MESSAGE = "模型调用失败，请重试。";

/** `unproductive` / `LLM_UNPRODUCTIVE`。 */
export const LLM_UNPRODUCTIVE_MESSAGE =
  "工具连续无有效进展或参数无效，请重试。";

/** `degraded` / `LLM_EMPTY_RESPONSE`。 */
export const LLM_EMPTY_RESPONSE_MESSAGE = "模型返回空内容，请重试。";

/** `interrupted` / `TURN_INTERRUPTED`（发下一条即可重试）。 */
export const TURN_INTERRUPTED_EMPTY_MESSAGE =
  "已中断。直接发送下一条即可重试。";
