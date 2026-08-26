/**
 * 空响应 / 空失败脸的协议文案 —— 四句 finish 兜底。
 *
 * 只收两端已经逐字对齐的映射。`describeError` / `errorActionForCode` /
 * 会话连通性计数 / StreamError 类是各端实现核，不进本文件。
 */

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
